import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.accounts import CalendarAccounts
from core.marks import PersonalMarks, list_messages, parse_date
from core.store import CalendarStore, ConflictError, ValidationError, dumps


def test_legacy_binding_is_permanent_and_each_user_has_a_private_store(tmp_path):
    accounts = CalendarAccounts(tmp_path)
    original = PersonalMarks(accounts.legacy).add("20261112", "红色", "已有记录")[
        "mark"
    ]
    with pytest.raises(ValidationError, match="先在"):
        accounts.for_user("22222", "")
    admin = accounts.for_user("11111", "11111")
    assert PersonalMarks(admin).list() == [original]
    user = accounts.for_user("22222", "11111")
    assert PersonalMarks(user).list() == []
    own = PersonalMarks(user).add("20261112", "黑色", "另一用户")["mark"]
    for delete in (True, False):
        with pytest.raises(ValidationError, match="不属于"):
            PersonalMarks(user).change(original["id"], "盗改", delete=delete)
    changed_admin, identity = accounts.for_web("22222")
    assert PersonalMarks(changed_admin).list() == [own]
    assert identity["scope"] == "qq:22222"
    assert PersonalMarks(accounts.for_user("11111", "22222")).list() == [original]
    assert PersonalMarks(
        CalendarAccounts(tmp_path).for_user("11111", "22222")
    ).list() == [original]
    with pytest.raises(ValidationError, match="填写管理员"):
        accounts.for_web("")
    with pytest.raises(ConflictError):
        accounts.for_web("22222", {"scope": "qq:11111"}, writing=True)


@pytest.mark.parametrize("qq", ["/etc/passwd", "001111", 123456, "abc", "1234", ""])
def test_invalid_admin_configuration_never_binds_or_creates_user_data(tmp_path, qq):
    accounts = CalendarAccounts(tmp_path)
    with pytest.raises(ValidationError):
        accounts.for_user("11111", qq)
    assert not (tmp_path / "users").exists()
    assert accounts.for_web("")[1]["scope"] == "legacy:unbound"


def test_marker_identity_layers_comments_and_deletion_preserve_other_records(tmp_path):
    store = CalendarStore(tmp_path / "calendar.sqlite3")
    marks = PersonalMarks(store)
    first = marks.add("20261112", "红色", "奖金")["mark"]
    second = marks.add("2026-11-12", "red", "提成")["mark"]
    assert first["id"] == second["id"]
    assert second["comment"] == "奖金\n提成"
    for _ in range(4):
        third = marks.add("20261112", "红", "提成")["mark"]
    assert third["layers"] == 3 and third["comment"] == second["comment"]
    other = marks.add("20261112", "黑色", "计划开销")["mark"]
    store.mutate(
        {
            "revision": store.snapshot()["revision"],
            "operation": "note.save",
            "target": "2026-11-12",
            "id": "event",
            "text": "保留事件",
            "color": "#123456",
        }
    )
    changed = marks.change(first["id"], "新的注释，含 空格\n第二行")["mark"]
    assert changed["id"] == first["id"] and changed["color"] == first["color"]
    assert changed["layers"] == 3 and changed["date"] == first["date"]
    assert marks.change(first["id"], "")["mark"]["comment"] == ""
    marks.change(first["id"], delete=True)
    assert marks.list() == [other]
    assert store.snapshot()["items"]["2026-11-12"]["notes"][0]["text"] == "保留事件"
    assert CalendarStore(store.path).snapshot() == store.snapshot()


def test_commands_and_web_share_ids_comments_and_revision(tmp_path):
    store = CalendarStore(tmp_path / "calendar.sqlite3")
    web = store.mutate(
        {
            "revision": 0,
            "operation": "paint",
            "target": "2026-11-11",
            "pen_id": "income",
        }
    )
    mark_id = web["item"]["marks"][0]["id"]
    marks = PersonalMarks(store)
    marks.change(mark_id, "从 QQ 修改")
    with pytest.raises(ConflictError):
        store.mutate(
            {
                "revision": web["revision"],
                "operation": "mark.save",
                "target": web["target"],
                "id": mark_id,
                "comment": "旧页面",
            }
        )
    updated = store.mutate(
        {
            "revision": store.snapshot()["revision"],
            "operation": "mark.save",
            "target": web["target"],
            "id": mark_id,
            "comment": "管理页新注释",
            "color": "#000000",
        }
    )
    assert marks.list()[0]["comment"] == "管理页新注释"
    assert marks.list()[0]["color"] == "#ce4545"
    store.mutate(
        {
            "revision": updated["revision"],
            "operation": "mark.delete",
            "target": web["target"],
            "id": mark_id,
        }
    )
    assert marks.list() == []


@pytest.mark.parametrize(
    "value",
    ["20260229", "20261301", "202611", "2026-11", "19000101", "21000101", "明天", None],
)
def test_command_date_validation(value):
    with pytest.raises(ValidationError):
        parse_date(value)


def test_all_records_listed_and_long_comments_are_not_lost(tmp_path):
    marks = PersonalMarks(CalendarStore(tmp_path / "calendar.sqlite3"))
    for day in range(1, 32):
        marks.add(f"202701{day:02d}", "红色", str(day) + "长注释" * 600)
    records = marks.list()
    messages = list_messages(records)
    assert len(records) == 31 and all(len(m) <= 1500 for m in messages)
    joined = "".join(messages)
    for record in records:
        assert record["id"] in joined and record["comment"] in joined
        assert any(f"ID：{record['id']}" in message for message in messages)


def test_color_limit_and_failed_append_are_atomic(tmp_path):
    store = CalendarStore(tmp_path / "calendar.sqlite3")
    marks = PersonalMarks(store)
    for color in ("红色", "黑色", "蓝色", "#123456"):
        marks.add("20261112", color, "已保存")
    before = store.snapshot()
    with pytest.raises(ValidationError, match="四支"):
        marks.add("20261112", "紫色", "不能保存")
    assert store.snapshot() == before  # New pen is also rolled back.
    marks.change(marks.list()[0]["id"], "甲" * 4000)
    before = store.snapshot()
    with pytest.raises(ValidationError):
        marks.add("20261112", "红色", "乙")
    assert store.snapshot() == before


def test_standard_color_words_cannot_be_shadowed_by_renamed_pens(tmp_path):
    store = CalendarStore(tmp_path / "calendar.sqlite3")
    store.mutate(
        {
            "revision": 0,
            "operation": "pen.save",
            "id": "income",
            "name": "红色",
            "color": "#3978c6",
            "shape": "diamond",
        }
    )
    marks = PersonalMarks(store)
    result = marks.add("20261112", "红色")["mark"]
    assert result["color"] == "#ce4545"
    pens = store.catalog()["pens"]
    assert len({p["name"] for p in pens}) == len(pens)
    result = marks.add("20261113", "破财")["mark"]
    assert result["color"] == "#252a34"


def test_concurrent_user_and_dashboard_writes_keep_one_stable_id(tmp_path):
    marks = PersonalMarks(CalendarStore(tmp_path / "calendar.sqlite3"))
    with ThreadPoolExecutor(max_workers=6) as workers:
        results = list(
            workers.map(
                lambda n: marks.add("20261112", "红色", f"第{n}项")["mark"], range(12)
            )
        )
    assert len({r["id"] for r in results}) == 1
    assert marks.list()[0]["layers"] == 3
    assert len(marks.list()[0]["comment"].splitlines()) == 12


def test_schema_upgrade_keeps_verified_backup_and_stable_ids(tmp_path):
    path = tmp_path / "calendar.sqlite3"
    store = CalendarStore(path)
    record = store.mutate(
        {
            "revision": 0,
            "operation": "paint",
            "target": "2026-11-11",
            "pen_id": "income",
        }
    )
    legacy = record["item"]
    legacy["marks"][0].pop("id")
    legacy["marks"][0].pop("comment")
    with store.connection() as db:
        db.execute("UPDATE items SET data=?", (dumps(legacy),))
        db.execute("PRAGMA user_version=1")
    upgraded = CalendarStore(path)
    backups = list(tmp_path.glob("calendar.v1-backup-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert json.loads(db.execute("SELECT data FROM items").fetchone()[0]) == legacy
    mark = PersonalMarks(upgraded).list()[0]
    assert mark["comment"] == "" and mark["layers"] == 1
    assert CalendarStore(path).snapshot() == upgraded.snapshot()
    assert len(list(tmp_path.glob("calendar.v1-backup-*.sqlite3"))) == 1
