"""AstrBot contract harness, not a live AstrBot installation."""

import asyncio
import importlib
import json
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture
def plugin(monkeypatch, tmp_path):
    names = [
        "astrbot",
        "astrbot.api",
        "astrbot.api.event",
        "astrbot.api.star",
        "astrbot.api.web",
        "astrbot.core",
        "astrbot.core.utils",
        "astrbot.core.utils.astrbot_path",
    ]
    modules = {name: types.ModuleType(name) for name in names}
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    def tag(attribute, value):
        def decorate(function):
            setattr(function, attribute, value)
            return function

        return decorate

    class Star:
        def __init__(self, context):
            self.context = context

    class Context:
        def __init__(self):
            self.routes = []

        def register_web_api(self, *args):
            self.routes.append(args)

    class Request:
        query = {}
        payload = {}
        raw = b"{}"

        async def body(self):
            return self.raw

        async def json(self, default=None):
            return self.payload

    modules["astrbot.api"].AstrBotConfig = dict
    modules["astrbot.api"].logger = types.SimpleNamespace(exception=lambda *_: None)
    modules["astrbot.api.event"].AstrMessageEvent = object
    modules["astrbot.api.event"].filter = types.SimpleNamespace(
        PermissionType=types.SimpleNamespace(ADMIN="admin"),
        permission_type=lambda value: tag("permission", value),
        command=lambda value: tag("command_name", value),
        llm_tool=lambda name: tag("tool_name", name),
    )
    modules["astrbot.api.star"].Star = Star
    modules["astrbot.api.star"].Context = Context
    request = Request()
    modules["astrbot.api.web"].request = request
    modules["astrbot.api.web"].json_response = lambda body: {"code": 200, "body": body}
    modules["astrbot.api.web"].error_response = lambda message, status_code=400: {
        "code": status_code,
        "message": message,
    }
    modules["astrbot.core.utils.astrbot_path"].get_astrbot_data_path = lambda: str(
        tmp_path
    )
    package_name = "_calendar_entry_test"
    package = types.ModuleType(package_name)
    package.__path__ = [str(Path(__file__).resolve().parents[1])]
    monkeypatch.setitem(sys.modules, package_name, package)
    entry = importlib.import_module(f"{package_name}.main")
    context = Context()
    instance = entry.CalendarPlugin(context, {"enable_command": True})
    yield instance, request, context, tmp_path
    for name in list(sys.modules):
        if name.startswith(package_name + "."):
            sys.modules.pop(name, None)


def run(coro):
    return asyncio.run(coro)


def test_registered_routes_and_data_live_outside_plugin(plugin):
    instance, request, context, tmp_path = plugin
    assert {r[0] for r in context.routes} == {
        "/astrbot_plugin_calendar/month",
        "/astrbot_plugin_calendar/mutate",
        "/astrbot_plugin_calendar/export",
        "/astrbot_plugin_calendar/import/preview",
        "/astrbot_plugin_calendar/import",
    }
    request.query = {"year": "2026", "month": "11"}
    result = run(instance.api_month())
    assert result["code"] == 200
    assert len(result["body"]["days"]) == 42
    assert (
        instance.store.path
        == tmp_path / "plugin_data" / "astrbot_plugin_calendar" / "calendar.sqlite3"
    )


def test_save_conflict_export_and_invalid_body(plugin):
    instance, request, _, _ = plugin
    request.payload = {
        "scope": "legacy:unbound",
        "revision": 0,
        "operation": "paint",
        "target": "2026-11-11",
        "pen_id": "income",
    }
    request.raw = json.dumps(request.payload).encode()
    assert run(instance.api_mutate())["code"] == 200
    assert run(instance.api_mutate())["code"] == 409
    request.query = {"scope": "legacy:unbound"}
    record = run(instance.api_export())["body"]
    assert record["items"]["2026-11-11"]["marks"][0]["layers"] == 1
    request.payload = []
    assert run(instance.api_mutate())["code"] == 400
    request.payload = None
    assert run(instance.api_mutate())["code"] == 400
    request.raw = b"x" * 65537
    assert run(instance.api_mutate())["code"] == 413


def test_invalid_date_and_storage_failure_are_controlled(plugin):
    instance, request, _, _ = plugin
    request.query = {"year": "2026", "month": "13"}
    assert run(instance.api_month())["code"] == 400

    def failing():
        raise OSError("private diagnostic")

    result = run(instance._run(failing))
    assert result["code"] == 500
    assert "private diagnostic" not in result["message"]


def test_import_preview_commit_conflict_invalid_and_body_limit(plugin):
    instance, request, _, _ = plugin
    request.payload = {
        "scope": "legacy:unbound",
        "revision": 0,
        "text": json.dumps(
            {
                "format": "liji-import-v1",
                "entries": [
                    {
                        "date": "2026-11-11",
                        "marks": [{"pen": "得财", "layers": 2}],
                        "events": [{"text": "计划：复盘"}],
                    }
                ],
            }
        ),
    }
    request.raw = json.dumps(request.payload).encode()
    preview = run(instance.api_import_preview())
    assert preview["code"] == 200
    assert preview["body"]["summary"]["events_added"] == 1
    assert instance.store.export()["items"] == {}
    assert run(instance.api_import())["code"] == 200
    assert run(instance.api_import())["code"] == 409
    assert run(instance.api_import_preview())["code"] == 409
    request.payload = None
    assert run(instance.api_import())["code"] == 400
    assert run(instance.api_import_preview())["code"] == 400
    request.raw = b"x" * (2 * 1024 * 1024 + 1)
    assert run(instance.api_import())["code"] == 413
    assert run(instance.api_import_preview())["code"] == 413


def test_initialization_is_safe_under_parallel_requests(plugin):
    instance, _, _, _ = plugin

    async def initialize():
        await asyncio.gather(*(instance.initialize() for _ in range(8)))

    run(initialize())
    assert instance.store.snapshot([])["revision"] == 0


def test_admin_command_help_date_validation_and_no_private_notes(plugin):
    instance, _, _, _ = plugin
    assert instance.calendar_command.permission == "admin"
    assert instance.calendar_command.command_name == "历记"
    event = types.SimpleNamespace(plain_result=lambda text: text)

    async def collect(argument=""):
        return [part async for part in instance.calendar_command(event, argument)]

    assert "可视化页面" in run(collect())[0]
    assert "无效日期" in run(collect("2026-02-29"))[0]
    assert "完整日期" in run(collect("2026"))[0]
    assert "春节" in run(collect("2026-02-17"))[0]
    instance.store.mutate(
        {
            "revision": 0,
            "operation": "note.save",
            "target": "2026-02-17",
            "id": "private",
            "text": "私人备注不得发送",
            "color": "#123456",
        }
    )
    assert "私人备注" not in run(collect("2026-02-17"))[0]
    instance.config["enable_command"] = False
    assert run(collect()) == []


class QQEvent:
    def __init__(self, message="", qq="11111", platform="aiocqhttp"):
        self.message = message
        self.qq = qq
        self.platform = platform
        self.images = []

    def get_message_str(self):
        return self.message

    def get_sender_id(self):
        return self.qq

    def get_platform_name(self):
        return self.platform

    def plain_result(self, text):
        return text

    def image_result(self, path):
        return {"image": path}

    async def send(self, message):
        path = message["image"]
        await asyncio.sleep(0)
        self.images.append((path, Path(path).read_bytes()))


async def collect_handler(handler, event):
    return [part async for part in handler(event)]


def test_qq_command_full_lifecycle_optional_comments_and_isolation(plugin):
    instance, _, _, _ = plugin
    instance.config["admin_qq"] = "11111"
    event = QQEvent("/标记 20261112 红色 奖金  第一行\n第二行")
    result = run(collect_handler(instance.mark_command, event))
    assert "2026-11-12" in result[0] and "奖金  第一行\n第二行" in result[0]
    record = json.loads(run(instance.tool_list_marks(event)))["result"][0]
    other = QQEvent(qq="22222")
    assert json.loads(run(instance.tool_list_marks(other)))["result"] == []
    assert (
        json.loads(run(instance.tool_update_mark(other, record["id"], "他人内容")))[
            "ok"
        ]
        is False
    )
    assert (
        json.loads(run(instance.tool_delete_mark(other, record["id"])))["ok"] is False
    )
    event.message = f"/修改标记 {record['id']} 新的 注释\n下一行"
    assert (
        "日期和颜色保持不变"
        in run(collect_handler(instance.update_mark_command, event))[0]
    )
    updated = json.loads(run(instance.tool_list_marks(event)))["result"][0]
    assert (
        updated["color"] == record["color"]
        and updated["comment"] == "新的 注释\n下一行"
    )
    event.message = "/标记"
    assert record["id"] in run(collect_handler(instance.mark_command, event))[0]
    event.message = f"修改标记 {record['id']} --清空"
    run(collect_handler(instance.update_mark_command, event))
    assert (
        json.loads(run(instance.tool_list_marks(event)))["result"][0]["comment"] == ""
    )
    event.message = f"/删除标记 {record['id']}"
    assert "全部层数" in run(collect_handler(instance.delete_mark_command, event))[0]
    event.message = "标记 20261112 黑色"
    assert "注释：无" in run(collect_handler(instance.mark_command, event))[0]


@pytest.mark.parametrize(
    "command,message,fragment",
    [
        ("mark_command", "/标记 20261112", "用法"),
        ("mark_command", "/标记 20260229 红色", "无效日期"),
        ("mark_command", "/标记 20261112 奇怪颜色", "颜色"),
        ("update_mark_command", "/修改标记", "用法"),
        ("update_mark_command", "/修改标记 abc", "用法"),
        ("delete_mark_command", "/删除标记 abc 多余参数", "用法"),
        ("delete_mark_command", "/删除标记 nobody", "不属于"),
    ],
)
def test_qq_invalid_commands_are_readable(plugin, command, message, fragment):
    instance, _, _, _ = plugin
    instance.config["admin_qq"] = "11111"
    assert (
        fragment
        in run(collect_handler(getattr(instance, command), QQEvent(message)))[0]
    )


def test_qq_identity_and_tool_switches_fail_closed(plugin):
    instance, _, _, _ = plugin
    event = QQEvent("/标记")
    assert "填写管理员 QQ" in run(collect_handler(instance.mark_command, event))[0]
    instance.config["admin_qq"] = "11111"
    for platform in ("webchat", "qq_official", "telegram", "satori"):
        result = json.loads(
            run(
                instance.tool_add_mark(
                    QQEvent(platform=platform), "20261112", "红色", ""
                )
            )
        )
        assert result["ok"] is False and "身份" in result["error"]
    instance.config["enable_ai_tools"] = False
    assert "关闭日历 AI" in run(instance.tool_list_marks(event))
    instance.config["enable_ai_tools"] = True
    instance.config["enable_qq_commands"] = False
    assert "关闭 QQ" in run(instance.tool_list_marks(event))
    assert "关闭 QQ" in run(collect_handler(instance.mark_command, event))[0]


def test_web_and_admin_qq_sync_and_stale_page_cannot_write_new_account(plugin):
    instance, request, _, _ = plugin
    instance.config["admin_qq"] = "11111"
    first = run(instance.api_month())["body"]
    record = json.loads(
        run(instance.tool_add_mark(QQEvent(), "20261112", "红色", "QQ注释"))
    )["result"]["mark"]
    request.query = {"year": "2026", "month": "11"}
    page = run(instance.api_month())["body"]
    assert page["items"]["2026-11-12"]["marks"][0]["comment"] == "QQ注释"
    request.payload = {
        "scope": page["scope"],
        "revision": page["revision"],
        "operation": "mark.save",
        "target": "2026-11-12",
        "id": record["id"],
        "comment": "页面注释",
    }
    assert run(instance.api_mutate())["code"] == 200
    assert "页面注释" in run(instance.tool_list_marks(QQEvent()))
    instance.config["admin_qq"] = "22222"
    request.payload["revision"] = 0  # Even if revisions happen to be equal.
    assert run(instance.api_mutate())["code"] == 409
    assert run(instance.api_import_preview())["code"] == 409
    assert run(instance.api_import())["code"] == 409
    request.query["scope"] = first["scope"]
    assert run(instance.api_export())["code"] == 409
    assert run(instance.api_month())["body"]["items"] == {}
    assert "页面注释" in run(instance.tool_list_marks(QQEvent()))
    assert (
        json.loads(run(instance.tool_list_marks(QQEvent(qq="22222"))))["result"] == []
    )


def test_current_month_image_and_ai_image_render_locally_with_cleanup(plugin):
    instance, _, _, tmp_path = plugin
    instance.config["admin_qq"] = "11111"
    event = QQEvent("/日历")
    assert run(collect_handler(instance.month_command, event)) == []
    assert "已向当前会话发送" in run(instance.tool_show_month(event))
    assert len(event.images) == 2
    for path, content in event.images:
        assert content.startswith(b"\x89PNG")
        assert not Path(path).exists()
        assert Path(path).is_relative_to(tmp_path)


def test_ai_parameters_match_documented_schema_and_never_accept_owner(plugin):
    import inspect
    import re

    instance, _, _, _ = plugin
    for name in (
        "tool_add_mark",
        "tool_update_mark",
        "tool_delete_mark",
        "tool_list_marks",
        "tool_show_month",
    ):
        handler = getattr(instance, name)
        params = set(inspect.signature(handler).parameters) - {"event"}
        assert not params.intersection({"qq", "owner", "user_id", "admin_qq"})
        schema_names = set(
            re.findall(r"^\s+(\w+)\(string\):", handler.__doc__, re.MULTILINE)
        )
        assert schema_names == params
        assert handler.tool_name.startswith("calendar_")


def test_image_send_failure_cleans_up_and_returns_controlled_error(plugin):
    instance, _, _, _ = plugin
    instance.config["admin_qq"] = "11111"

    class FailingEvent(QQEvent):
        async def send(self, message):
            await super().send(message)
            raise OSError("private adapter detail")

    event = FailingEvent("/日历")
    result = run(collect_handler(instance.month_command, event))
    assert "发送失败" in result[0] and "private adapter" not in result[0]
    assert not Path(event.images[0][0]).exists()
