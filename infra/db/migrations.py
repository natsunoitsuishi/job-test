"""基于 PRAGMA user_version 的顺序迁移。新增 schema 时在 MIGRATIONS 末尾追加 (版本号, 语句列表)。"""
from __future__ import annotations

import contextlib
import sqlite3

from .models import SCHEMA_V1

MIGRATIONS: list[tuple[int, list[str]]] = [
    (1, SCHEMA_V1),
]

LATEST_VERSION = MIGRATIONS[-1][0]


def current_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def migrate(conn: sqlite3.Connection) -> int:
    """conn 必须是 isolation_level=None（自动提交）模式。返回迁移后的版本号。"""
    cur = current_version(conn)
    for ver, stmts in MIGRATIONS:
        if ver <= cur:
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            for s in stmts:
                conn.execute(s)
            conn.execute(f"PRAGMA user_version={int(ver)}")
            conn.execute("COMMIT")
        except BaseException:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise
        cur = ver
    return cur
