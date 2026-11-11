"""Strict, additive text imports with a read-only preview and atomic commit."""

import json
import re
from uuid import uuid4

from .store import ConflictError, ValidationError, color, dumps, target_key, text

IMPORT_FORMAT = "liji-import-v1"
MAX_IMPORT_BYTES = 512 * 1024
MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_ENTRIES = 500


def _object(value, allowed, name):
    if not isinstance(value, dict):
        raise ValidationError(f"{name}必须是 JSON 对象")
    unknown = value.keys() - allowed
    if unknown:
        raise ValidationError(f"{name}含不支持的字段：{'、'.join(sorted(unknown))}")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(f"JSON 字段重复：{key}")
        result[key] = value
    return result


def parse_import(source, catalog):
    if not isinstance(source, str) or not source.strip():
        raise ValidationError("请粘贴批量导入文本")
    try:
        size = len(source.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValidationError("导入文本包含无效字符") from exc
    if size > MAX_IMPORT_BYTES:
        raise ValidationError("导入文本最多 512 KiB，请分批导入")
    source = source.strip().removeprefix("\ufeff").strip()
    fence = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", source, re.DOTALL | re.I)
    if fence:
        source = fence[1]
    try:
        document = json.loads(source, object_pairs_hook=_unique_object)
        # JSON escapes can hide lone surrogates until SQLite/HTTP encodes the result.
        json.dumps(document, ensure_ascii=False).encode("utf-8")
    except json.JSONDecodeError as exc:
        raise ValidationError(
            f"JSON 格式错误：第 {exc.lineno} 行、第 {exc.colno} 列；请检查引号和逗号"
        ) from exc
    except UnicodeEncodeError as exc:
        raise ValidationError("JSON 内容包含无效字符") from exc
    except (RecursionError, ValueError) as exc:
        if isinstance(exc, ValidationError):
            raise
        raise ValidationError("JSON 内容无效或嵌套过深") from exc
    _object(document, {"format", "entries"}, "导入内容")
    if document.get("format") != IMPORT_FORMAT:
        raise ValidationError(f"format 必须为 {IMPORT_FORMAT}，请使用批量导入模板")
    entries = document.get("entries")
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_ENTRIES:
        raise ValidationError(f"entries 应包含 1–{MAX_ENTRIES} 条记录")
    pens = {pen["name"]: pen for pen in catalog["pens"]}
    cleaned = []
    for index, entry in enumerate(entries, 1):
        try:
            _object(entry, {"date", "marks", "events"}, "记录")
            key = target_key(entry.get("date"))
            marks = entry.get("marks", [])
            events = entry.get("events", [])
            if not isinstance(marks, list) or len(marks) > 4:
                raise ValidationError("marks 必须是数组，每条最多四种画笔")
            if not isinstance(events, list) or len(events) > 50:
                raise ValidationError("events 必须是数组，每条最多 50 个事件")
            if not marks and not events:
                raise ValidationError("至少填写一个标记或事件")
            normalized_marks, normalized_events = [], []
            used_pens = set()
            for mark in marks:
                _object(mark, {"pen", "layers"}, "标记")
                name = text(mark.get("pen"), "画笔名称", 24)
                if name not in pens:
                    raise ValidationError(
                        f"画笔「{name}」不存在，请先新建画笔或使用模板中的名称"
                    )
                if name in used_pens:
                    raise ValidationError(
                        f"同一条记录中画笔「{name}」重复，请合并 layers"
                    )
                used_pens.add(name)
                layers = mark.get("layers", 1)
                if type(layers) is not int or not 1 <= layers <= 3:
                    raise ValidationError("layers 必须是 1–3 的整数")
                normalized_marks.append(
                    {"pen_id": pens[name]["id"], "pen": name, "layers": layers}
                )
            for event in events:
                _object(event, {"text", "color"}, "事件")
                normalized_events.append(
                    {
                        "text": text(event.get("text"), "事件正文", 4000),
                        "color": color(event.get("color", catalog["note_color"])),
                    }
                )
            cleaned.append(
                {"date": key, "marks": normalized_marks, "events": normalized_events}
            )
        except ValidationError as exc:
            raise ValidationError(f"第 {index} 条记录：{exc}") from exc
    return cleaned


class CalendarImporter:
    def __init__(self, store):
        self.store = store

    def run(self, payload, *, commit=False):
        if not isinstance(payload, dict):
            raise ValidationError("请求必须为 JSON 对象")
        if type(payload.get("revision")) is not int:
            raise ValidationError("缺少有效的数据版本，请刷新")
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE" if commit else "BEGIN")
            revision, catalog = self.store._state(db)
            if payload["revision"] != revision:
                raise ConflictError(
                    "日历已在另一页面更新，请刷新并重新预览；导入文本仍保留"
                )
            entries = parse_import(payload.get("text"), catalog)
            items, changed = {}, set()
            summary = {
                "entries": len(entries),
                "targets": 0,
                "changed_targets": 0,
                "mark_layers_added": 0,
                "events_added": 0,
                "skipped": 0,
            }
            rows = []
            for index, entry in enumerate(entries, 1):
                key = entry["date"]
                if key not in items:
                    items[key] = self.store._item(db, key)
                item = items[key]
                row = {"date": key, "marks": [], "events": []}
                try:
                    for mark in entry["marks"]:
                        old = next(
                            (m for m in item["marks"] if m["pen_id"] == mark["pen_id"]),
                            None,
                        )
                        added = max(0, mark["layers"] - (old["layers"] if old else 0))
                        for _ in range(added):
                            self.store._change_item(
                                "paint", mark, item, catalog, revision
                            )
                            # Keep the eraser's actual last-painted order, even within one import.
                            revision += 1
                        summary["mark_layers_added"] += added
                        summary["skipped"] += int(added == 0)
                        row["marks"].append(
                            {
                                "pen": mark["pen"],
                                "layers": mark["layers"],
                                "added": added,
                            }
                        )
                        if added:
                            changed.add(key)
                    for event in entry["events"]:
                        exists = any(
                            n["text"] == event["text"] and n["color"] == event["color"]
                            for n in item["notes"]
                        )
                        if not exists:
                            self.store._change_item(
                                "note.save",
                                {**event, "id": uuid4().hex},
                                item,
                                catalog,
                                revision,
                            )
                            summary["events_added"] += 1
                            changed.add(key)
                        else:
                            summary["skipped"] += 1
                        row["events"].append({**event, "added": not exists})
                except ValidationError as exc:
                    raise ValidationError(
                        f"第 {index} 条记录（{key}）：{exc}；本批次未写入"
                    ) from exc
                rows.append(row)
            summary["targets"] = len(items)
            summary["changed_targets"] = len(changed)
            original_revision = payload["revision"]
            if commit and changed:
                for key in sorted(changed):
                    db.execute(
                        "INSERT INTO items VALUES (?, ?) ON CONFLICT(target) DO UPDATE SET data=excluded.data",
                        (key, dumps(items[key])),
                    )
                # Reserve all paint-order values so subsequent normal strokes remain newer.
                revision = max(original_revision + 1, revision)
                db.execute("UPDATE state SET revision=? WHERE id=1", (revision,))
            else:
                revision = original_revision
            result = {"revision": revision, "summary": summary, "rows": rows}
            if commit:
                result.update(catalog=catalog, items=items, message="批量导入完成")
            return result
