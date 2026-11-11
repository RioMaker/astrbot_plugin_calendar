"""Bind the legacy calendar once; select personal stores without copying records."""

import hashlib
import re
import sqlite3
import threading
from pathlib import Path

from .store import CalendarStore, ConflictError, ValidationError


def qq_number(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"[1-9][0-9]{4,19}", value.strip()
    ):
        raise ValidationError("请在插件配置中填写有效的管理员 QQ 号（5–20 位数字）")
    return value.strip()


class CalendarAccounts:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.legacy = CalendarStore(self.root / "calendar.sqlite3")
        self._lock = threading.RLock()
        self._stores = {}
        with sqlite3.connect(self.root / "accounts.sqlite3") as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS binding (id INTEGER PRIMARY KEY CHECK(id=1), qq TEXT NOT NULL)"
            )

    def bind(self, admin_qq):
        """An empty configuration is permitted only before the first binding."""
        admin = qq_number(admin_qq) if admin_qq else ""
        with sqlite3.connect(self.root / "accounts.sqlite3", timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT qq FROM binding WHERE id=1").fetchone()
            if not row and admin:
                db.execute("INSERT INTO binding VALUES (1, ?)", (admin,))
                row = (admin,)
            if row and not admin:
                raise ValidationError("请填写管理员 QQ；已有个人日历仍保留")
            return row[0] if row else ""

    def for_user(self, qq, admin_qq):
        owner = self.bind(admin_qq)
        if not owner:
            raise ValidationError(
                "请管理员先在历记插件配置中填写管理员 QQ，再使用日历和标记指令"
            )
        return self._store(qq_number(qq), owner)

    def _store(self, qq, legacy_owner):
        if qq == legacy_owner:
            return self.legacy
        with self._lock:
            if qq not in self._stores:
                digest = hashlib.sha256(f"qq:{qq}".encode()).hexdigest()
                self._stores[qq] = CalendarStore(
                    self.root / "users" / f"{digest}.sqlite3"
                )
            return self._stores[qq]

    def for_web(self, admin_qq, payload=None, *, writing=False):
        owner = self.bind(admin_qq)
        admin = qq_number(admin_qq) if admin_qq else ""
        scope = f"qq:{admin}" if admin else "legacy:unbound"
        if writing:
            if not isinstance(payload, dict):
                raise ValidationError("请求必须为 JSON 对象")
            if payload.get("scope") != scope:
                raise ConflictError(
                    "管理页面绑定的 QQ 已变化或页面已过期，请重新打开页面后再操作"
                )
        store = self._store(admin, owner) if admin else self.legacy
        return store, {
            "scope": scope,
            "account": {"qq": admin, "configured": bool(admin)},
        }
