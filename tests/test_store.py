from concurrent.futures import ThreadPoolExecutor

import pytest

from core.store import (
    CalendarStore,
    ConflictError,
    ValidationError,
    empty_item,
    target_key,
)


@pytest.fixture
def store(tmp_path):
    return CalendarStore(tmp_path / "calendar.sqlite3")


def change(store, operation, **payload):
    return store.mutate(
        {"revision": store.snapshot([])["revision"], "operation": operation, **payload}
    )


def test_three_layers_cap_erase_and_restart_persistence(store):
    for _ in range(4):
        result = change(store, "paint", target="2026-11-11", pen_id="income")
    assert result["item"]["marks"][0]["layers"] == 3
    assert "上限" in result["message"]
    reopened = CalendarStore(store.path)
    assert reopened.snapshot()["items"]["2026-11-11"]["marks"][0]["layers"] == 3
    for expected in [2, 1, 0]:
        result = change(reopened, "erase", target="2026-11-11")
        assert sum(m["layers"] for m in result["item"]["marks"]) == expected
    assert "2026-11-11" not in reopened.snapshot()["items"]


def test_eraser_uses_actual_stroke_order_and_pen_specific_erase(store):
    for pen in ["income", "expense", "income"]:
        change(store, "paint", target="2026", pen_id=pen)
    result = change(store, "erase", target="2026")
    assert {m["pen_id"]: m["layers"] for m in result["item"]["marks"]} == {
        "income": 1,
        "expense": 1,
    }
    result = change(store, "erase", target="2026")
    assert result["item"]["marks"][0]["pen_id"] == "income"
    change(store, "paint", target="2026", pen_id="expense")
    result = change(store, "erase", target="2026", pen_id="income")
    assert [m["pen_id"] for m in result["item"]["marks"]] == ["expense"]


def test_year_month_and_day_marks_are_independent(store):
    for key in ["2026", "2026-11", "2026-11-11", "2027-11-11"]:
        change(store, "paint", target=key, pen_id="income")
    change(store, "erase", target="2026-11")
    assert set(store.snapshot()["items"]) == {"2026", "2026-11-11", "2027-11-11"}


def test_multinote_edit_color_and_delete_are_independent(store):
    for note_id, text, color in [
        ("first", "收到款项", "#CE4545"),
        ("second", "<script>不是 HTML</script>\n第二行", "#658773"),
    ]:
        change(
            store, "note.save", target="2026-11-11", id=note_id, text=text, color=color
        )
    change(
        store,
        "note.save",
        target="2026-11-11",
        id="first",
        text="已确认到账",
        color="#2456AB",
    )
    notes = store.snapshot()["items"]["2026-11-11"]["notes"]
    assert len(notes) == 2
    assert notes[0]["text"] == "已确认到账"
    assert notes[0]["color"] == "#2456ab"
    assert notes[1]["text"].startswith("<script>")
    change(store, "note.delete", target="2026-11-11", id="first")
    assert len(store.snapshot()["items"]["2026-11-11"]["notes"]) == 1


def test_retries_update_note_by_id_instead_of_duplicating(store):
    for _ in range(3):
        change(
            store,
            "note.save",
            target="2026",
            id="same-id",
            text="年度计划",
            color="#112233",
        )
    assert len(store.snapshot()["items"]["2026"]["notes"]) == 1


def test_concurrent_edit_conflicts_instead_of_losing_data(store):
    def save(note_id):
        try:
            return store.mutate(
                {
                    "revision": 0,
                    "operation": "note.save",
                    "target": "2026-11-11",
                    "id": note_id,
                    "text": note_id,
                    "color": "#112233",
                }
            )
        except ConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ["one", "two"]))
    assert results.count("conflict") == 1
    assert len(store.snapshot()["items"]["2026-11-11"]["notes"]) == 1
    assert store.snapshot([])["revision"] == 1


@pytest.mark.parametrize(
    "bad",
    [
        "2026-02-29",
        "2026-00",
        "2026-13-01",
        "2026-2-01",
        "2026-11-31",
        "1900",
        "2100",
        "../2026",
        None,
        [],
        "２０２６",
    ],
)
def test_targets_are_strict(bad):
    with pytest.raises(ValidationError):
        target_key(bad)


@pytest.mark.parametrize(
    "payload",
    [
        {"operation": "paint", "target": "2026", "pen_id": "missing"},
        {"operation": "paint", "target": "2026", "pen_id": []},
        {
            "operation": "note.save",
            "target": "2026",
            "id": "n",
            "text": " ",
            "color": "#112233",
        },
        {
            "operation": "note.save",
            "target": "2026",
            "id": "n",
            "text": "x",
            "color": "red;script",
        },
        {
            "operation": "pen.save",
            "id": "p",
            "name": "p",
            "shape": [],
            "color": "#112233",
        },
        {
            "operation": "target.save",
            "target": "2026-11-11",
            "values": {},
            "holiday": {"status": []},
        },
        {
            "operation": "target.save",
            "target": "2026-11-11",
            "values": {},
            "festivals": [1],
        },
        {"operation": []},
        {"operation": "unsupported"},
    ],
)
def test_invalid_mutation_rolls_back_entire_transaction(store, payload):
    before = store.export()
    with pytest.raises(ValidationError):
        store.mutate({"revision": 0, **payload})
    assert store.export() == before


def test_custom_conditions_keep_false_and_reject_invalid_select_state(store):
    change(store, "field.save", id="done", name="已复盘", kind="boolean", options=[])
    change(
        store,
        "field.save",
        id="mood",
        name="心情",
        kind="select",
        options=["开心", "平静"],
    )
    result = change(
        store, "target.save", target="2026-11", values={"done": False, "mood": "平静"}
    )
    assert result["item"]["values"] == {"done": False, "mood": "平静"}
    with pytest.raises(ValidationError):
        change(store, "target.save", target="2026-11", values={"mood": "不在选项"})
    with pytest.raises(ValidationError):
        change(store, "field.delete", id="done")
    with pytest.raises(ValidationError):
        change(
            store, "field.save", id="mood", name="心情", kind="select", options=["开心"]
        )
    with pytest.raises(ValidationError):
        change(store, "field.save", id="done", name="已复盘", kind="text", options=[])
    change(
        store,
        "field.save",
        id="mood",
        name="我的心情",
        kind="select",
        options=["开心", "平静", "期待"],
    )
    change(store, "target.save", target="2026-11", values={})
    change(store, "field.delete", id="done")


def test_pen_change_updates_catalog_and_cannot_delete_used_pen(store):
    change(store, "paint", target="2026-11", pen_id="income")
    change(
        store, "pen.save", id="income", name="收获", color="#112233", shape="diamond"
    )
    assert store.catalog()["pens"][0]["shape"] == "diamond"
    with pytest.raises(ValidationError):
        change(store, "pen.delete", id="income")
    change(store, "erase", target="2026-11")
    change(store, "pen.delete", id="income")
    assert not any(p["id"] == "income" for p in store.catalog()["pens"])


def test_manual_holiday_and_custom_festival_can_return_to_auto(store):
    result = change(
        store,
        "target.save",
        target="2099-01-01",
        values={},
        festivals=["纪念日", "纪念日"],
        holiday={"status": "holiday", "name": "公司安排"},
    )
    assert result["item"]["festivals"] == ["纪念日"]
    assert result["item"]["holiday"]["name"] == "公司安排"
    result = change(
        store, "target.save", target="2099-01-01", values={}, festivals=[], holiday=None
    )
    assert result["item"] == empty_item()


def test_empty_and_boolean_revisions_are_rejected(store):
    for payload in [
        [],
        None,
        {"revision": True},
        {"operation": "erase", "target": "2026"},
    ]:
        with pytest.raises(ValidationError):
            store.mutate(payload)


def test_newer_database_is_not_silently_reset(store):
    with store.connection() as db:
        db.execute("PRAGMA user_version=999")
    with pytest.raises(RuntimeError):
        CalendarStore(store.path)
    with store.connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 999
