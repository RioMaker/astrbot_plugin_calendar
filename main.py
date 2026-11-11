"""AstrBot entry point. The dashboard supplies authentication for every API."""

import asyncio
import re
from datetime import date
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.api.web import error_response, json_response, request
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .core.accounts import CalendarAccounts
from .core.calendar import CalendarService, date_info, today
from .core.importing import MAX_REQUEST_BYTES, CalendarImporter
from .core.marks import PersonalMarks, format_mark, list_messages, message_chunks
from .core.picture import month_image
from .core.store import ConflictError, ValidationError, target_key

PLUGIN_NAME = "astrbot_plugin_calendar"


class CalendarPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.store = None
        self.accounts = None
        self._init_lock = asyncio.Lock()
        self._image_slots = asyncio.Semaphore(2)
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
                self.accounts = await asyncio.to_thread(CalendarAccounts, path.parent)
                self.store = self.accounts.legacy

    def _web_operation(self, operation, payload=None, *, writing=False):
        store, account = self.accounts.for_web(
            self.config.get("admin_qq", ""), payload, writing=writing
        )
        return {**operation(store), **account}

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
        return await self._run(
            lambda: self._web_operation(
                lambda store: CalendarService(store).month(year, month)
            )
        )

    async def api_mutate(self):
        if len(await request.body()) > 65536:
            return error_response("请求内容过大", status_code=413)
        payload = await request.json(default=None)
        return await self._run(
            lambda: self._web_operation(
                lambda store: store.mutate(payload), payload, writing=True
            )
        )

    async def api_export(self):
        payload = {"scope": request.query.get("scope")}
        return await self._run(
            lambda: self._web_operation(
                lambda store: store.export(), payload, writing=True
            )
        )

    async def _import_request(self, *, commit):
        if len(await request.body()) > MAX_REQUEST_BYTES:
            return error_response("请求内容过大，请分批导入", status_code=413)
        payload = await request.json(default=None)
        return await self._run(
            lambda: self._web_operation(
                lambda store: CalendarImporter(store).run(payload, commit=commit),
                payload,
                writing=True,
            )
        )

    async def _personal_store(self, event, *, ai=False):
        if not self.config.get("enable_qq_commands", True):
            raise ValidationError("管理员已关闭 QQ 日历与标记功能")
        if ai and not self.config.get("enable_ai_tools", True):
            raise ValidationError("管理员已关闭日历 AI 工具")
        # OneBot supplies the real QQ number. Other adapters may have unrelated
        # numeric IDs or QQ OpenIDs, which must never alias a QQ calendar.
        if event.get_platform_name() != "aiocqhttp":
            raise ValidationError(
                "个人日历目前支持 OneBot v11 QQ（如 NapCat / Lagrange）；此平台无法确认真实 QQ 身份"
            )
        sender = str(event.get_sender_id())
        if not re.fullmatch(r"[1-9][0-9]{4,19}", sender):
            raise ValidationError("无法确认当前消息的 QQ 身份")
        await self.initialize()
        return await asyncio.to_thread(
            self.accounts.for_user, sender, self.config.get("admin_qq", "")
        )

    @staticmethod
    def _arguments(event, command):
        # AstrBot has already checked the command/wake prefix. Read the remaining
        # text ourselves so optional comments preserve spaces and line breaks.
        raw = event.get_message_str().strip()
        matched = re.fullmatch(
            r"[/／]?" + re.escape(command) + r"(?:\s+([\s\S]*))?", raw
        )
        if not matched:
            raise ValidationError(f"请使用 /{command} 指令")
        return (matched.group(1) or "").strip()

    async def _mark_action(self, event, action, *args, ai=False):
        store = await self._personal_store(event, ai=ai)
        marks = PersonalMarks(store)
        if action == "list":
            return await asyncio.to_thread(marks.list)
        if action == "add":
            return await asyncio.to_thread(marks.add, *args)
        if action == "update":
            return await asyncio.to_thread(marks.change, *args)
        return await asyncio.to_thread(marks.change, *args, delete=True)

    @filter.command("日历")
    async def month_command(self, event: AstrMessageEvent):
        """发送当前 QQ 用户的当月日历图片（UTC+8）。"""
        try:
            await self._send_month(event)
        except ValidationError as exc:
            yield event.plain_result(str(exc))
        except Exception:
            logger.exception("历记月历图片失败")
            yield event.plain_result("日历图片发送失败，请查看插件日志后重试")

    async def _send_month(self, event, *, ai=False):
        path = None
        try:
            store = await self._personal_store(event, ai=ai)
            current = today()
            async with self._image_slots:
                snapshot = await asyncio.to_thread(
                    CalendarService(store).month, current.year, current.month
                )
                path = await asyncio.to_thread(
                    month_image, snapshot, self.accounts.root / "images"
                )
            # Await the adapter's send before deleting the file. Yielding a path
            # can defer transmission until after an async generator is closed.
            await event.send(event.image_result(str(path)))
        finally:
            if path:
                path.unlink(missing_ok=True)

    @filter.command("标记")
    async def mark_command(self, event: AstrMessageEvent):
        """列出自己的标记，或 /标记 YYYYMMDD 颜色 [注释]。"""
        try:
            argument = self._arguments(event, "标记")
            if not argument:
                records = await self._mark_action(event, "list")
                for message in list_messages(records):
                    yield event.plain_result(message)
                return
            parts = argument.split(maxsplit=2)
            if len(parts) < 2:
                raise ValidationError(
                    "用法：/标记 20261112 红色 [可选注释]；直接发送 /标记 查看全部标记"
                )
            result = await self._mark_action(
                event, "add", parts[0], parts[1], parts[2] if len(parts) == 3 else ""
            )
            for message in message_chunks(
                result["message"] + "\n" + format_mark(result["mark"])
            ):
                yield event.plain_result(message)
        except ValidationError as exc:
            yield event.plain_result(str(exc))
        except Exception:
            logger.exception("历记标记操作失败")
            yield event.plain_result("标记操作失败，请查看插件日志后重试")

    async def _change_command(self, event, *, delete):
        command = "删除标记" if delete else "修改标记"
        try:
            parts = self._arguments(event, command).split(maxsplit=1)
            if (delete and len(parts) != 1) or (not delete and len(parts) != 2):
                raise ValidationError(
                    f"用法：/{command} ID"
                    + (
                        ""
                        if delete
                        else " 注释内容；用 --清空 清除注释。日期和颜色不可修改"
                    )
                )
            if delete:
                result = await self._mark_action(event, "delete", parts[0])
            else:
                result = await self._mark_action(
                    event, "update", parts[0], "" if parts[1] == "--清空" else parts[1]
                )
            for message in message_chunks(
                result["message"] + "\n" + format_mark(result["mark"])
            ):
                yield event.plain_result(message)
        except ValidationError as exc:
            yield event.plain_result(str(exc))
        except Exception:
            logger.exception("历记修改标记失败")
            yield event.plain_result("标记操作失败，请查看插件日志后重试")

    @filter.command("修改标记")
    async def update_mark_command(self, event: AstrMessageEvent):
        """根据 ID 修改自己标记的注释；日期和颜色不可修改。"""
        async for result in self._change_command(event, delete=False):
            yield result

    @filter.command("删除标记")
    async def delete_mark_command(self, event: AstrMessageEvent):
        """根据 ID 删除自己的整条标记，保留当天其他颜色和事件。"""
        async for result in self._change_command(event, delete=True):
            yield result

    async def _tool_result(self, event, action, *args):
        import json

        try:
            result = await self._mark_action(event, action, *args, ai=True)
            return json.dumps({"ok": True, "result": result}, ensure_ascii=False)
        except ValidationError as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
        except Exception:
            logger.exception("历记 AI 标记操作失败")
            return '{"ok":false,"error":"操作失败，请查看插件日志后重试"}'

    @filter.llm_tool(name="calendar_list_marks")
    async def tool_list_marks(self, event: AstrMessageEvent):
        """列出当前消息发送者的全部日历标记，含 ID、日期、颜色、层数及注释。只能访问本人数据。"""
        return await self._tool_result(event, "list")

    @filter.llm_tool(name="calendar_add_mark")
    async def tool_add_mark(
        self, event: AstrMessageEvent, date_text: str, color: str, comment: str = ""
    ):
        """给当前消息发送者添加日历标记。同日期同颜色增加一层，最多三层；新注释追加，其他颜色共存。仅在用户要求添加时使用。

        Args:
            date_text(string): 公历日期 YYYYMMDD 或 YYYY-MM-DD，必须为明确的年月日。
            color(string): 中文颜色、英文颜色名、#RRGGBB 或已有画笔名称。
            comment(string): 可选注释，留空表示不新增注释。
        """
        return await self._tool_result(event, "add", date_text, color, comment)

    @filter.llm_tool(name="calendar_update_mark")
    async def tool_update_mark(
        self, event: AstrMessageEvent, mark_id: str, comment: str
    ):
        """修改当前发送者指定标记的注释，保留日期、颜色、层数。先查询真实 ID，仅在用户要求修改时使用。

        Args:
            mark_id(string): 从本人标记清单取得的 ID，不能猜测。
            comment(string): 完整的新注释，空字符串表示清空。
        """
        return await self._tool_result(event, "update", mark_id, comment)

    @filter.llm_tool(name="calendar_delete_mark")
    async def tool_delete_mark(self, event: AstrMessageEvent, mark_id: str):
        """删除当前发送者的一整条标记及其全部层数；保留其他标记和事件。先查询真实 ID，仅在用户明确要求删除时使用。

        Args:
            mark_id(string): 从本人标记清单取得的 ID，不能猜测。
        """
        return await self._tool_result(event, "delete", mark_id)

    @filter.llm_tool(name="calendar_show_month")
    async def tool_show_month(self, event: AstrMessageEvent):
        """向当前会话发送消息发送者本人的当月日历图片。仅在用户要求查看日历时调用。"""
        try:
            await self._send_month(event, ai=True)
            return "已向当前会话发送你的当月日历图片。"
        except ValidationError as exc:
            return str(exc)
        except Exception:
            logger.exception("历记 AI 月历图片失败")
            return "日历图片发送失败，请查看插件日志后重试"

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
                "红色标记默认表示得财，黑色标记默认表示破财；标记显示在数字旁，层数以 1–3 表示，多种标记独立排列。\n"
                "可用 /历记 YYYY-MM-DD 查询日期基础信息；个人月历图片用 /日历，添加或查询个人标记用 /标记。"
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
