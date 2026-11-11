"""AstrBot entry point. The dashboard supplies authentication for every API."""

import asyncio
from datetime import date
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.api.web import error_response, json_response, request
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .core.calendar import CalendarService, date_info, today
from .core.importing import MAX_REQUEST_BYTES, CalendarImporter
from .core.store import CalendarStore, ConflictError, ValidationError, target_key

PLUGIN_NAME = "astrbot_plugin_calendar"


class CalendarPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.store = None
        self.service = None
        self.importer = None
        self._init_lock = asyncio.Lock()
        for path, handler, methods, description in [
            ("month", self.api_month, ["GET"], "读取标准月历"),
            ("mutate", self.api_mutate, ["POST"], "保存日历标记、备注及状态"),
            ("export", self.api_export, ["GET"], "导出日历记录"),
            ("import/preview", self.api_import_preview, ["POST"], "预览批量导入"),
            ("import", self.api_import, ["POST"], "批量导入标记与事件"),
        ]:
            context.register_web_api(
                f"/{PLUGIN_NAME}/{path}", handler, methods, description
            )

    async def initialize(self):
        async with self._init_lock:
            if self.store is None:
                path = (
                    Path(get_astrbot_data_path())
                    / "plugin_data"
                    / PLUGIN_NAME
                    / "calendar.sqlite3"
                )
                self.store = await asyncio.to_thread(CalendarStore, path)
                self.service = CalendarService(self.store)
                self.importer = CalendarImporter(self.store)

    async def _run(self, operation, *args):
        try:
            await self.initialize()
            result = await asyncio.to_thread(operation, *args)
            return json_response(result)
        except ConflictError as exc:
            return error_response(str(exc), status_code=409)
        except ValidationError as exc:
            return error_response(str(exc), status_code=400)
        except Exception:
            logger.exception("历记操作失败")
            return error_response("日历操作失败，请检查插件日志后重试", status_code=500)

    async def api_month(self):
        current = today()
        # Read the request proxy before entering the worker thread.
        year = request.query.get("year", str(current.year))
        month = request.query.get("month", str(current.month))
        return await self._run(lambda: self.service.month(year, month))

    async def api_mutate(self):
        if len(await request.body()) > 65536:
            return error_response("请求内容过大", status_code=413)
        payload = await request.json(default=None)
        return await self._run(lambda: self.store.mutate(payload))

    async def api_export(self):
        return await self._run(lambda: self.store.export())

    async def _import_request(self, *, commit):
        if len(await request.body()) > MAX_REQUEST_BYTES:
            return error_response("请求内容过大，请分批导入", status_code=413)
        payload = await request.json(default=None)
        return await self._run(lambda: self.importer.run(payload, commit=commit))

    async def api_import_preview(self):
        return await self._import_request(commit=False)

    async def api_import(self):
        return await self._import_request(commit=True)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("历记")
    async def calendar_command(self, event: AstrMessageEvent, date_text: str = ""):
        """打开日历说明，或以 /历记 YYYY-MM-DD 查询日期基础信息。"""
        if not self.config.get("enable_command", True):
            return
        if not date_text:
            yield event.plain_result(
                "历记：请在 AstrBot WebUI → 插件 → 历记 → 可视化页面 → calendar 中编辑。\n"
                "红圈默认表示得财，黑圈默认表示破财；同一画笔最多三层。\n"
                "可用 /历记 YYYY-MM-DD 查询日期基础信息，备注只在管理页面显示。"
            )
            return
        try:
            target_key(date_text)
            if len(date_text) != 10:
                raise ValidationError("请输入完整日期 YYYY-MM-DD")
            await self.initialize()
            catalog = await asyncio.to_thread(self.store.catalog)
            info = await asyncio.to_thread(
                date_info, date.fromisoformat(date_text), catalog["year_boundary"]
            )
            pillars = " / ".join(
                f"{label}{info['ganzhi'][key]['text']}"
                for key, label in [("year", "年"), ("month", "月"), ("day", "日")]
            )
            yield event.plain_result(
                f"{info['date']} {info['weekday_label']} · 农历{info['lunar']}\n{pillars}\n"
                f"节日／节气：{'、'.join(info['festivals'] + ([info['solar_term']] if info['solar_term'] else [])) or '无'}"
            )
        except ValidationError as exc:
            yield event.plain_result(str(exc))
        except Exception:
            logger.exception("历记日期查询失败")
            yield event.plain_result("日期查询失败，请查看插件日志")
