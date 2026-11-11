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
        "revision": 0,
        "operation": "paint",
        "target": "2026-11-11",
        "pen_id": "income",
    }
    request.raw = json.dumps(request.payload).encode()
    assert run(instance.api_mutate())["code"] == 200
    assert run(instance.api_mutate())["code"] == 409
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
