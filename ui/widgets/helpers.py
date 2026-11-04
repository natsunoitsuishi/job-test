from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional, Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem

from infra.logging import get_logger

logger = get_logger("app")
_TASKS: set[asyncio.Future] = set()


def spawn(coro, on_error: Optional[Callable[[BaseException], None]] = None) -> asyncio.Future:
    """在 qasync 事件循环里启动协程；保留引用避免被回收，异常记日志并回调。"""
    task = asyncio.ensure_future(coro)
    _TASKS.add(task)

    def _done(t: asyncio.Future) -> None:
        _TASKS.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.error("界面任务异常: %r", exc)
            if on_error is not None:
                on_error(exc)

    task.add_done_callback(_done)
    return task


def make_table(headers: Sequence[str], editable: bool = False) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(list(headers))
    t.verticalHeader().setVisible(False)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    if not editable:
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.horizontalHeader().setStretchLastSection(True)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    return t


def fill_table(table: QTableWidget, rows: Sequence[Sequence[Any]],
               colors: Optional[Sequence[Optional[str]]] = None) -> None:
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, v in enumerate(row):
            item = QTableWidgetItem("" if v is None else str(v))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if colors and colors[r]:
                item.setBackground(QColor(colors[r]))  # type: ignore[arg-type]
            table.setItem(r, c, item)


def status_color(status: str) -> str:
    return "#d5f5e3" if status == "OK" else "#fadbd8"
