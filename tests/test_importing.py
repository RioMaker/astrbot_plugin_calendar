import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from core.importing import MAX_IMPORT_BYTES, CalendarImporter
from core.store import CalendarStore, ConflictError, ValidationError


@pytest.fixture
def store(tmp_path):
    return CalendarStore(tmp_path / "import.sqlite3")


def source(entries):
    return json.dumps(
        {"format": "liji-import-v1", "entries": entries}, ensure_ascii=False
    )


def entry(key="2026-11-11", pen="得财", layers=2, event="计划：项目复盘"):
    return {
        "date": key,
        "marks": [{"pen": pen, "layers": layers}],
        "events": [{"text": event}],
    }


def run(store, entries, *, commit=False, revision=None):
    return CalendarImporter(store).run(
        {
            "revision": store.snapshot([])["revision"]
            if revision is None
            else revision,
            "text": source(entries),
        },
        commit=commit,
    )


def test_preview_commit_persistence_idempotence_and_periods(store):
    entries = [entry(), entry("2026-12-01", "破财", 3), entry("2026"), entry("2026-11")]
    before = store.export()
    preview = run(store, entries)
    assert store.export() == before
    assert preview["summary"] == {
        "entries": 4,
        "targets": 4,
        "changed_targets": 4,
        "mark_layers_added": 9,
        "events_added": 4,
        "skipped": 0,
    }
    result = run(store, entries, commit=True)
    assert result["summary"] == preview["summary"]
    snapshot = CalendarStore(store.path).export()
    assert len(snapshot["items"]) == 4
    assert snapshot["items"]["2026-11-11"]["marks"][0]["layers"] == 2
    assert snapshot["items"]["2026-12-01"]["notes"][0]["color"] == "#658773"
    again = run(store, entries, commit=True)
    assert again["summary"]["skipped"] == 8
    assert again["summary"]["changed_targets"] == 0
    assert store.export() == snapshot


def test_existing_content_and_larger_layer_counts_are_kept(store):
    store.mutate(
        {
            "revision": 0,
            "operation": "target.save",
            "target": "2026-11-11",
            "values": {},
            "festivals": ["纪念日"],
            "holiday": {"status": "holiday", "name": "自定安排"},
        }
    )
    run(store, [entry(layers=3, event="已有事件")], commit=True)
    result = run(store, [entry(layers=1, event="新事件")], commit=True)
    item = store.snapshot()["items"]["2026-11-11"]
    assert item["marks"][0]["layers"] == 3
    assert [n["text"] for n in item["notes"]] == ["已有事件", "新事件"]
    assert item["festivals"] == ["纪念日"]
    assert item["holiday"]["name"] == "自定安排"
    assert result["summary"]["mark_layers_added"] == 0
    assert result["summary"]["events_added"] == 1


def test_repeated_entries_merge_and_different_event_colors_are_kept(store):
    entries = [entry(layers=1), entry(layers=3), entry(layers=2)]
    entries.append(
        {
            "date": "2026-11-11",
            "events": [{"text": "计划：项目复盘", "color": "#AABBCC"}],
        }
    )
    result = run(store, entries, commit=True)
    assert result["summary"]["mark_layers_added"] == 3
    assert result["summary"]["events_added"] == 2
    assert result["summary"]["skipped"] == 3
    assert result["items"]["2026-11-11"]["notes"][1]["color"] == "#aabbcc"


def test_fenced_json_bom_defaults_multiline_and_events_only(store):
    document = source(
        [
            {"date": "2028-02-29", "marks": [{"pen": "得财"}]},
            {
                "date": "2026-12",
                "events": [{"text": '计划：第一行\n第二行："引号" | 逗号，😀'}],
            },
        ]
    )
    payload = {"revision": 0, "text": "\ufeff```json\r\n" + document + "\r\n```"}
    result = CalendarImporter(store).run(payload, commit=True)
    assert result["items"]["2028-02-29"]["marks"][0]["layers"] == 1
    assert result["items"]["2026-12"]["notes"][0]["text"].endswith("逗号，😀")


def test_shipped_json_and_prompt_examples_are_importable(store):
    docs = Path(__file__).resolve().parents[1] / "docs"
    prompt = (docs / "ai-import-prompt.txt").read_text(encoding="utf-8")
    for raw in [
        (docs / "import-template.json").read_text(encoding="utf-8"),
        prompt[prompt.index("{\n") :],
    ]:
        result = CalendarImporter(store).run({"revision": 0, "text": raw})
        assert result["summary"]["events_added"] > 0
    assert store.snapshot()["items"] == {}


def test_storage_error_rolls_back_all_written_targets(store):
    with store.connection() as db:
        db.execute(
            "CREATE TRIGGER fail_import BEFORE INSERT ON items WHEN NEW.target='2026-12-01' BEGIN SELECT RAISE(ABORT, 'simulated disk error'); END"
        )
    before = store.export()
    with pytest.raises(sqlite3.DatabaseError):
        run(store, [entry(), entry("2026-12-01")], commit=True)
    assert store.export() == before


@pytest.mark.parametrize(
    "invalid,match",
    [
        ({"date": "2026-02-29", "events": [{"text": "无效日期"}]}, "无效日期"),
        ({"date": "2100", "marks": [{"pen": "得财"}]}, "1901"),
        ({"date": "2026-1-1", "marks": [{"pen": "得财"}]}, "有效的年"),
        ({"date": "2026-11-11", "marks": [{"pen": "未知"}]}, "不存在"),
        ({"date": "2026-11-11", "marks": [{"pen": "得财", "layers": True}]}, "整数"),
        ({"date": "2026-11-11", "marks": [{"pen": "得财", "layers": 1.5}]}, "整数"),
        ({"date": "2026-11-11", "marks": [{"pen": "得财", "layers": 4}]}, "整数"),
        ({"date": "2026-11-11", "marks": [{"pen": "得财", "layers": 0}]}, "整数"),
        ({"date": "2026-11-11", "marks": [{"pen": "得财"}, {"pen": "得财"}]}, "重复"),
        ({"date": "2026-11-11", "marks": "得财"}, "数组"),
        ({"date": "2026-11-11", "events": "事件"}, "数组"),
        (
            {"date": "2026-11-11", "events": [{"text": "事件", "color": "red"}]},
            "RRGGBB",
        ),
        ({"date": "2026-11-11", "events": [{"text": " "}]}, "长度"),
        ({"date": "2026-11-11", "events": [{"text": "字" * 4001}]}, "长度"),
        (
            {
                "date": "2026-11-11",
                "events": [{"text": "事件", "title": "被误写的字段"}],
            },
            "不支持的字段",
        ),
        ({"date": "2026-11-11", "notes": [{"text": "错误字段"}]}, "不支持的字段"),
        ({"date": "2026-11-11"}, "至少"),
        (None, "对象"),
    ],
)
def test_invalid_later_entry_never_writes_earlier_entries(store, invalid, match):
    before = store.export()
    for commit in (False, True):
        with pytest.raises(ValidationError, match=f"第 2 条记录.*{match}"):
            run(store, [entry(), invalid], commit=commit)
        assert store.export() == before


@pytest.mark.parametrize(
    "raw,match",
    [
        (None, "粘贴"),
        ("", "粘贴"),
        ("[]", "对象"),
        ('{"format":"liji-calendar","entries":[]}', "模板"),
        ('{"format":"liji-import-v1","entries":[]}', "1–500"),
        ('{"format":"liji-import-v1","entries":[] ,}', "第 1 行"),
        ('{"format":"liji-import-v1","format":"liji-import-v1","entries":[]}', "重复"),
        ('[{"x":' * 1200, "嵌套过深"),
        ("x" * (MAX_IMPORT_BYTES + 1), "512 KiB"),
        ("字" * (MAX_IMPORT_BYTES // 3 + 1), "512 KiB"),
        ("\ud800", "无效字符"),
        (
            '{"format":"liji-import-v1","entries":[{"date":"2026-11-11","events":[{"text":"\\ud800"}]}]}',
            "无效字符",
        ),
    ],
    ids=[
        "null",
        "empty",
        "array",
        "wrong-format",
        "empty-entries",
        "syntax",
        "duplicate-key",
        "deep",
        "large-ascii",
        "large-utf8",
        "surrogate",
        "escaped-surrogate",
    ],
)
def test_invalid_documents_are_controlled(store, raw, match):
    with pytest.raises(ValidationError, match=match):
        CalendarImporter(store).run({"revision": 0, "text": raw}, commit=True)
    assert store.snapshot()["revision"] == 0


def test_entry_limit_and_notes_limit_rollback(store):
    with pytest.raises(ValidationError, match="1–500"):
        run(store, [entry()] * 501, commit=True)
    fifty = {"date": "2026-11-11", "events": [{"text": f"事件{i}"} for i in range(50)]}
    run(store, [fifty], commit=True)
    before = store.export()
    with pytest.raises(ValidationError, match="第 2 条记录.*50"):
        run(store, [entry("2026-12-12"), entry()], commit=True)
    assert store.export() == before
    assert run(store, [fifty], commit=True)["summary"]["events_added"] == 0


def test_maximum_batch_can_be_previewed_and_committed(store):
    entries = [
        {
            "date": f"{2000 + i // 12}-{i % 12 + 1:02d}",
            "events": [{"text": "计划：月度复盘"}],
        }
        for i in range(500)
    ]
    result = run(store, entries, commit=True)
    assert result["summary"]["events_added"] == 500
    assert len(store.snapshot()["items"]) == 500


def test_pen_limit_is_checked_against_existing_marks_and_catalog_names(store):
    for i in range(3):
        store.mutate(
            {
                "operation": "pen.save",
                "revision": store.snapshot([])["revision"],
                "id": f"pen{i}",
                "name": f"画笔{i}",
                "color": "#123456",
                "shape": "square",
            }
        )
    entries = [entry(pen=name) for name in ["得财", "破财", "画笔0", "画笔1"]]
    run(store, entries, commit=True)
    before = store.export()
    with pytest.raises(ValidationError, match="四支画笔"):
        run(store, [entry("2026-12-12"), entry(pen="画笔2")], commit=True)
    assert store.export() == before


def test_batch_paint_order_and_next_normal_paint_erase_correctly(store):
    run(
        store,
        [entry(layers=1), entry(pen="破财", layers=2), entry(layers=3)],
        commit=True,
    )

    def mutate(operation, **kwargs):
        return store.mutate(
            {
                "revision": store.snapshot([])["revision"],
                "target": "2026-11-11",
                "operation": operation,
                **kwargs,
            }
        )

    result = mutate("erase")
    assert [m["layers"] for m in result["item"]["marks"]] == [2, 2]
    mutate("paint", pen_id="expense")
    result = mutate("erase")
    assert [m["layers"] for m in result["item"]["marks"]] == [2, 2]


def test_conflicts_reject_stale_preview_and_concurrent_commit(store):
    run(store, [entry()])
    run(store, [entry("2026-12-12")], commit=True)
    with pytest.raises(ConflictError):
        run(store, [entry()], commit=True, revision=0)
    before = store.export()
    revision = before["revision"]

    def attempt():
        try:
            run(store, [entry()], commit=True, revision=revision)
            return "saved"
        except ConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: attempt(), range(2))) == ["conflict", "saved"]
    assert len(store.snapshot()["items"]["2026-11-11"]["notes"]) == 1


@pytest.mark.parametrize("payload", [None, [], {}, {"revision": True, "text": "{}"}])
def test_invalid_request_has_no_writes(store, payload):
    with pytest.raises(ValidationError):
        CalendarImporter(store).run(payload, commit=True)
    assert store.snapshot()["revision"] == 0
