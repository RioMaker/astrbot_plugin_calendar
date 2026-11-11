"""Transactional calendar annotations. No calendar data is stored in the code tree."""

import copy
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path

MIN_YEAR, MAX_YEAR = 1901, 2099
SHAPES = {"circle", "square", "diamond", "triangle"}
DEFAULT_CATALOG = {
    "title": "历记",
    "week_start": 0,
    "year_boundary": "spring_festival",
    "note_color": "#658773",
    "pens": [
        {"id": "income", "name": "得财", "color": "#ce4545", "shape": "circle"},
        {"id": "expense", "name": "破财", "color": "#252a34", "shape": "circle"},
    ],
    "fields": [],
}


class ValidationError(ValueError):
    pass


class ConflictError(ValidationError):
    pass


def text(value, name, maximum, *, empty=False):
    if not isinstance(value, str):
        raise ValidationError(f"{name}必须是文本")
    value = value.strip()
    if (not value and not empty) or len(value) > maximum:
        raise ValidationError(f"{name}长度应为{'0' if empty else '1'}–{maximum}字")
    return value


def color(value):
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise ValidationError("颜色必须为 #RRGGBB")
    return value.lower()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,48}", value):
        raise ValidationError("无效的记录标识")
    return value


def target_key(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{4}(?:-[0-9]{2}(?:-[0-9]{2})?)?", value
    ):
        raise ValidationError("请选择有效的年、月或日期")
    parts = [int(part) for part in value.split("-")]
    if not MIN_YEAR <= parts[0] <= MAX_YEAR:
        raise ValidationError(f"支持 {MIN_YEAR}–{MAX_YEAR} 年")
    try:
        date(*(parts + [1] * (3 - len(parts))))
    except ValueError as exc:
        raise ValidationError("无效日期") from exc
    return value


def empty_item():
    return {"marks": [], "notes": [], "values": {}, "festivals": [], "holiday": None}


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class CalendarStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError("日历数据库版本较新，请升级插件；现有数据未修改")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(
                "CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1),"
                " revision INTEGER NOT NULL, catalog TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS items (target TEXT PRIMARY KEY, data TEXT NOT NULL);"
            )
            db.execute(
                "INSERT OR IGNORE INTO state VALUES (1, 0, ?)",
                (dumps(DEFAULT_CATALOG),),
            )
            db.execute("PRAGMA user_version=1")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _state(db):
        revision, catalog = db.execute(
            "SELECT revision, catalog FROM state WHERE id=1"
        ).fetchone()
        return revision, json.loads(catalog)

    @staticmethod
    def _item(db, key):
        row = db.execute("SELECT data FROM items WHERE target=?", (key,)).fetchone()
        return json.loads(row[0]) if row else empty_item()

    def snapshot(self, keys=None):
        with self.connection() as db:
            db.execute("BEGIN")
            revision, catalog = self._state(db)
            if keys is None:
                rows = db.execute("SELECT target, data FROM items ORDER BY target")
            else:
                keys = list(dict.fromkeys(keys))
                placeholders = ",".join("?" for _ in keys)
                rows = db.execute(
                    f"SELECT target, data FROM items WHERE target IN ({placeholders})",
                    keys,
                )
            return {
                "revision": revision,
                "catalog": catalog,
                "items": {key: json.loads(data) for key, data in rows},
            }

    def mutate(self, payload):
        if not isinstance(payload, dict):
            raise ValidationError("请求必须为 JSON 对象")
        if type(payload.get("revision")) is not int:
            raise ValidationError("缺少有效的数据版本，请刷新")
        operation = payload.get("operation")
        if not isinstance(operation, str):
            raise ValidationError("缺少操作类型")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            revision, catalog = self._state(db)
            if payload["revision"] != revision:
                raise ConflictError(
                    "日历已在另一页面更新，请刷新后重试；当前输入仍保留"
                )
            key, item, message = None, None, "已保存"
            if operation in {
                "paint",
                "erase",
                "note.save",
                "note.delete",
                "target.save",
            }:
                key = target_key(payload.get("target"))
                item = self._item(db, key)
                message = self._change_item(operation, payload, item, catalog, revision)
                if item == empty_item():
                    db.execute("DELETE FROM items WHERE target=?", (key,))
                else:
                    db.execute(
                        "INSERT INTO items VALUES (?, ?) ON CONFLICT(target) DO UPDATE SET data=excluded.data",
                        (key, dumps(item)),
                    )
            elif operation in {"pen.save", "pen.delete", "field.save", "field.delete"}:
                self._change_catalog(db, operation, payload, catalog)
            elif operation == "settings.save":
                self._settings(payload, catalog)
            else:
                raise ValidationError("不支持的操作")
            revision += 1
            db.execute(
                "UPDATE state SET revision=?, catalog=? WHERE id=1",
                (revision, dumps(catalog)),
            )
            return {
                "revision": revision,
                "catalog": catalog,
                "target": key,
                "item": item,
                "message": message,
            }

    @staticmethod
    def _change_item(operation, payload, item, catalog, revision):
        if operation == "paint":
            pen_id = identifier(payload.get("pen_id"))
            if not any(p["id"] == pen_id for p in catalog["pens"]):
                raise ValidationError("画笔不存在，请重新选择")
            mark = next((m for m in item["marks"] if m["pen_id"] == pen_id), None)
            if mark and mark["layers"] == 3:
                return "该画笔已达到三层上限"
            if mark is None:
                # Keep multiple pens legible around the same numeral.
                if len(item["marks"]) >= 4:
                    raise ValidationError("每个数字最多同时使用四支画笔，请先擦除一支")
                mark = {"pen_id": pen_id, "layers": 0, "order": []}
                item["marks"].append(mark)
            mark["layers"] += 1
            mark["order"].append(revision + 1)
        elif operation == "erase":
            pen_id = payload.get("pen_id")
            if pen_id is not None:
                identifier(pen_id)
            marks = [
                m for m in item["marks"] if pen_id is None or m["pen_id"] == pen_id
            ]
            if not marks:
                return "这里没有可擦除的标记"
            mark = max(marks, key=lambda m: m["order"][-1])
            mark["layers"] -= 1
            mark["order"].pop()
            if not mark["layers"]:
                item["marks"].remove(mark)
            return "已擦除一层"
        elif operation == "note.save":
            note_id = identifier(payload.get("id"))
            note = {
                "id": note_id,
                "text": text(payload.get("text"), "备注", 4000),
                "color": color(payload.get("color")),
            }
            previous = next((n for n in item["notes"] if n["id"] == note_id), None)
            if previous is not None:
                previous.update(note)
            else:
                if len(item["notes"]) >= 50:
                    raise ValidationError("每个年、月或日最多保存 50 条备注")
                item["notes"].append(note)
        elif operation == "note.delete":
            note_id = identifier(payload.get("id"))
            item["notes"] = [n for n in item["notes"] if n["id"] != note_id]
        elif operation == "target.save":
            values = payload.get("values")
            if not isinstance(values, dict):
                raise ValidationError("扩展状态格式错误")
            fields = {f["id"]: f for f in catalog["fields"]}
            cleaned = {}
            for field_id, value in values.items():
                if field_id not in fields:
                    raise ValidationError("扩展字段已变化，请刷新")
                field = fields[field_id]
                if value is None or value == "":
                    continue
                if field["kind"] == "boolean":
                    if type(value) is not bool:
                        raise ValidationError("开关状态必须是布尔值")
                elif field["kind"] == "select":
                    if value not in field["options"]:
                        raise ValidationError("无效的状态选项")
                else:
                    value = text(value, field["name"], 200)
                cleaned[field_id] = value
            item["values"] = cleaned
            if len(payload["target"]) == 10:
                festivals = payload.get("festivals", [])
                if not isinstance(festivals, list) or len(festivals) > 10:
                    raise ValidationError("每天最多添加十个自定义节日")
                item["festivals"] = list(
                    dict.fromkeys(text(f, "节日", 40) for f in festivals)
                )
                holiday = payload.get("holiday")
                if holiday is not None:
                    if (
                        not isinstance(holiday, dict)
                        or not isinstance(holiday.get("status"), str)
                        or holiday.get("status")
                        not in {"holiday", "workday", "weekend", "normal", "unknown"}
                    ):
                        raise ValidationError("无效的放假或上班状态")
                    holiday = {
                        "status": holiday["status"],
                        "name": text(
                            holiday.get("name", ""), "安排说明", 80, empty=True
                        ),
                    }
                item["holiday"] = holiday
        return "已保存"

    @staticmethod
    def _used(db, entry_id, kind):
        for (data,) in db.execute("SELECT data FROM items"):
            item = json.loads(data)
            if kind == "pen" and any(m["pen_id"] == entry_id for m in item["marks"]):
                return True
            if kind == "field" and entry_id in item["values"]:
                return True
        return False

    def _change_catalog(self, db, operation, payload, catalog):
        kind, action = operation.split(".")
        collection = catalog["pens" if kind == "pen" else "fields"]
        entry_id = identifier(payload.get("id"))
        old = next((entry for entry in collection if entry["id"] == entry_id), None)
        if action == "delete":
            if self._used(db, entry_id, kind):
                raise ValidationError(
                    "此画笔或字段仍有记录，请先清除对应标记或状态再删除"
                )
            if old:
                collection.remove(old)
            return
        entry = {"id": entry_id, "name": text(payload.get("name"), "名称", 24)}
        if any(e["name"] == entry["name"] and e["id"] != entry_id for e in collection):
            raise ValidationError("名称已存在")
        if kind == "pen":
            shape = payload.get("shape")
            if not isinstance(shape, str) or shape not in SHAPES:
                raise ValidationError("不支持的记号形状")
            entry.update(color=color(payload.get("color")), shape=shape)
        else:
            field_kind = payload.get("kind")
            if not isinstance(field_kind, str) or field_kind not in {
                "text",
                "boolean",
                "select",
            }:
                raise ValidationError("无效的字段类型")
            options = payload.get("options", [])
            if not isinstance(options, list) or len(options) > 20:
                raise ValidationError("状态选项最多 20 个")
            options = list(dict.fromkeys(text(o, "状态选项", 30) for o in options))
            if field_kind == "select" and not options:
                raise ValidationError("请为下拉字段添加选项")
            entry.update(
                kind=field_kind, options=options if field_kind == "select" else []
            )
            if old and self._used(db, entry_id, kind):
                if old["kind"] != field_kind or not set(old["options"]).issubset(
                    options
                ):
                    raise ValidationError(
                        "已有状态记录：不能改变类型或移除旧选项，可以更名或增加选项"
                    )
        if old is not None:
            old.update(entry)
        else:
            if len(collection) >= 32:
                raise ValidationError("画笔或扩展字段最多各 32 个")
            collection.append(entry)

    @staticmethod
    def _settings(payload, catalog):
        week_start = payload.get("week_start")
        boundary = payload.get("year_boundary")
        if type(week_start) is not int or week_start not in (0, 6):
            raise ValidationError("每周应从周一或周日开始")
        if not isinstance(boundary, str) or boundary not in {
            "spring_festival",
            "lichun",
        }:
            raise ValidationError("无效的干支换年口径")
        catalog.update(
            title=text(payload.get("title"), "日历名称", 32),
            week_start=week_start,
            year_boundary=boundary,
            note_color=color(payload.get("note_color")),
        )

    def export(self):
        return {"format": "liji-calendar", "schema_version": 1, **self.snapshot()}

    def catalog(self):
        return copy.deepcopy(self.snapshot([])["catalog"])
