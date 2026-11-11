"""Shared, transactional personal mark operations for commands and AI tools."""

import json
import re
from uuid import uuid4

from .store import (
    ValidationError,
    color,
    dumps,
    empty_item,
    identifier,
    target_key,
    text,
)

COLORS = {
    "红色": ("#ce4545", "red", "红"),
    "黑色": ("#252a34", "black", "黑"),
    "蓝色": ("#3978c6", "blue", "蓝"),
    "绿色": ("#658773", "green", "绿"),
    "黄色": ("#d5a21b", "yellow", "黄"),
    "橙色": ("#df7a32", "orange", "橙"),
    "紫色": ("#8b5bb2", "purple", "紫"),
    "粉色": ("#d76b94", "pink", "粉"),
    "灰色": ("#78818b", "gray", "灰"),
    "白色": ("#ffffff", "white", "白"),
}


def parse_date(value):
    value = text(value, "日期", 10)
    if re.fullmatch(r"[0-9]{8}", value):
        value = f"{value[:4]}-{value[4:6]}-{value[6:]}"
    target_key(value)
    if len(value) != 10:
        raise ValidationError("请输入完整公历日期 YYYYMMDD 或 YYYY-MM-DD")
    return value


def color_label(value):
    return next(
        (name for name, aliases in COLORS.items() if aliases[0] == value), value
    )


def resolve_pen(catalog, value):
    value = text(value, "颜色", 24)
    resolved = next(
        (
            aliases[0]
            for name, aliases in COLORS.items()
            if value.lower() in (name, *aliases)
        ),
        None,
    )
    if resolved is None and not value.startswith("#"):
        # Standard color words always mean that color. Other existing pen names
        # preserve custom shapes and their established layer logic.
        named = next((p for p in catalog["pens"] if p["name"] == value), None)
        if named:
            return named
    resolved = resolved or color(value)
    existing = next((p for p in catalog["pens"] if p["color"] == resolved), None)
    if existing:
        return existing
    if len(catalog["pens"]) >= 32:
        raise ValidationError("画笔已达 32 支上限，请使用已有颜色或画笔名称")
    pen = {
        "id": uuid4().hex[:16],
        "name": color_label(resolved),
        "color": resolved,
        "shape": "circle",
    }
    # A renamed custom pen may already occupy a standard color name.
    if any(p["name"] == pen["name"] for p in catalog["pens"]):
        pen["name"] = f"{color_label(resolved)} {pen['id'][:6]}"
    catalog["pens"].append(pen)
    return pen


def mark_record(target, mark, catalog):
    pen = next(p for p in catalog["pens"] if p["id"] == mark["pen_id"])
    return {
        "id": mark["id"],
        "date": target,
        "color": pen["color"],
        "color_name": color_label(pen["color"]),
        "pen": pen["name"],
        "layers": mark["layers"],
        "comment": mark.get("comment", ""),
    }


class PersonalMarks:
    def __init__(self, store):
        self.store = store

    def list(self):
        snapshot = self.store.snapshot()
        return [
            mark_record(key, mark, snapshot["catalog"])
            for key, item in snapshot["items"].items()
            for mark in item["marks"]
        ]

    def _write(self, db, key, item, revision, catalog):
        if item == empty_item():
            db.execute("DELETE FROM items WHERE target=?", (key,))
        else:
            db.execute(
                "INSERT INTO items VALUES (?, ?) ON CONFLICT(target) DO UPDATE SET data=excluded.data",
                (key, dumps(item)),
            )
        db.execute(
            "UPDATE state SET revision=?, catalog=? WHERE id=1",
            (revision + 1, dumps(catalog)),
        )

    def add(self, date_text, color_text, comment=""):
        key = parse_date(date_text)
        comment = text(comment, "标记注释", 4000, empty=True)
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            revision, catalog = self.store._state(db)
            pen = resolve_pen(catalog, color_text)
            item = self.store._item(db, key)
            message = self.store._change_item(
                "paint", {"pen_id": pen["id"]}, item, catalog, revision
            )
            mark = next(m for m in item["marks"] if m["pen_id"] == pen["id"])
            if comment and comment not in mark["comment"].split("\n"):
                mark["comment"] = text(
                    "\n".join(filter(None, (mark["comment"], comment))),
                    "标记注释",
                    4000,
                )
            self._write(db, key, item, revision, catalog)
            return {"message": message, "mark": mark_record(key, mark, catalog)}

    def change(self, mark_id, comment=None, *, delete=False):
        mark_id = identifier(mark_id)
        if not delete:
            comment = text(comment, "标记注释", 4000, empty=True)
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            revision, catalog = self.store._state(db)
            for key, raw in db.execute(
                "SELECT target, data FROM items ORDER BY target"
            ).fetchall():
                item = json.loads(raw)
                mark = next((m for m in item["marks"] if m["id"] == mark_id), None)
                if mark is None:
                    continue
                if delete:
                    item["marks"].remove(mark)
                else:
                    mark["comment"] = comment
                self._write(db, key, item, revision, catalog)
                return {
                    "message": "已删除标记（全部层数）"
                    if delete
                    else "已修改注释，日期和颜色保持不变",
                    "mark": mark_record(key, mark, catalog),
                }
        raise ValidationError(
            "标记不存在或不属于当前用户，请使用 /标记 查看自己的标记 ID"
        )


def format_mark(mark):
    return f"ID：{mark['id']}\n{mark['date']} · {mark['color_name']} {mark['color']} · {mark['layers']} 层\n注释：{mark['comment'] or '无'}"


def list_messages(marks, limit=1500):
    if not marks:
        return [
            "你还没有标记。\n添加：/标记 20261112 红色 可选注释\n支持 YYYYMMDD / YYYY-MM-DD、中文颜色 / #RRGGBB。"
        ]
    content = (
        f"你的标记，共 {len(marks)} 条：\n\n"
        + "\n\n".join(map(format_mark, marks))
        + "\n\n修改注释：/修改标记 ID 内容\n清空注释：/修改标记 ID --清空\n删除整条：/删除标记 ID"
    )
    return message_chunks(content, limit)


def message_chunks(content, limit=1500):
    """Keep ID/date lines intact and split long comments without dropping text."""
    messages, current = [], ""
    for line in content.splitlines(keepends=True):
        if current and len(current) + len(line) > limit:
            messages.append(current)
            current = ""
        while len(line) > limit:
            messages.append(line[:limit])
            line = line[limit:]
        current += line
    if current:
        messages.append(current)
    return messages
