from __future__ import annotations

import os

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from infra.util.clock import fmt_ts
from ui.widgets.helpers import fill_table, make_table, spawn


def _t(ts) -> str:
    return fmt_ts(ts) if ts else ""


class AlarmPage(QWidget):
    def __init__(self, controller, vm) -> None:
        super().__init__()
        self.controller = controller
        self._last_active: object = None
        root = QVBoxLayout(self)
        ga = QGroupBox("当前报警")
        la = QVBoxLayout(ga)
        self.active = make_table(["报警码", "Key", "级别", "信息", "次数", "开始", "确认人"])
        la.addWidget(self.active)
        row = QHBoxLayout()
        b_ack, b_clear = QPushButton("确认选中"), QPushButton("清除全部(可清除项)")
        b_ack.clicked.connect(self._ack)
        b_clear.clicked.connect(self._clear)
        row.addWidget(b_ack)
        row.addWidget(b_clear)
        row.addStretch(1)
        la.addLayout(row)
        root.addWidget(ga, 1)
        gh = QGroupBox("历史报警（数据库）")
        QVBoxLayout(gh).addWidget(self._make_hist())
        root.addWidget(gh, 1)
        vm.snapshot_changed.connect(self.on_snapshot)
        self._timer = QTimer(self)
        self._timer.setInterval(3000)
        self._timer.timeout.connect(lambda: self.isVisible() and spawn(self.refresh_history()))
        self._timer.start()

    def _make_hist(self):
        self.hist = make_table(["报警码", "Key", "级别", "信息", "次数", "开始", "结束", "确认人"])
        return self.hist

    def on_snapshot(self, s: dict) -> None:
        rows = [[a["code"], a["key"], a["level"], a["message"], a["count"], _t(a["start_ts"]),
                 a["ack_user"] or ""] for a in s["alarms"]]
        if rows != self._last_active:           # 内容不变不重绘，避免选中行被清掉
            self._last_active = rows
            fill_table(self.active, rows, ["#fdebd0" if not r[6] else None for r in rows])

    def _ack(self) -> None:
        c, r = self.controller.container, self.active.currentRow()
        if c is None or r < 0:
            return
        c.runtime.submit_command("ack", code=self.active.item(r, 0).text(), key=self.active.item(r, 1).text(),
                                 user=os.environ.get("USER") or os.environ.get("USERNAME") or "operator")

    def _clear(self) -> None:
        c = self.controller.container
        if c is not None:
            c.runtime.submit_command("clear_alarms")

    async def refresh_history(self) -> None:
        c = self.controller.container
        if c is None:
            return
        rows = await c.repo.query_alarms(limit=100)
        fill_table(self.hist, [[r["alarm_code"], r["alarm_key"], r["alarm_level"], r["message"], r["count"],
                                _t(r["start_ts"]), _t(r["end_ts"]), r["ack_user"] or ""] for r in rows])
