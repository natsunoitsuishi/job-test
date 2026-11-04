from __future__ import annotations

import os

from PySide6.QtWidgets import (QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QVBoxLayout, QWidget)

from app.simulation import simulate_box_pass
from ui.widgets.helpers import fill_table, make_table, spawn, status_color
from ui.widgets.led import Led

_STATE_COLOR = {"RUNNING": "#27ae60", "READY": "#2980b9", "FAULT": "#c0392b"}


def _fmt(v, digits: int = 0) -> str:
    return "" if v in (None, 0) else f"{v:.{digits}f}"


class DashboardPage(QWidget):
    def __init__(self, controller, vm) -> None:
        super().__init__()
        self.controller = controller
        self._cam_leds: dict[str, Led] = {}
        self._last_rows: object = None
        root = QVBoxLayout(self)

        box = QGroupBox("运行状态")
        g = QGridLayout(box)
        self.lbl_state, self.lbl_mode = QLabel("-"), QLabel("-")
        self.lbl_session, self.lbl_active = QLabel("-"), QLabel("-")
        self.lbl_speed, self.lbl_queue = QLabel("-"), QLabel("-")
        for i, (title, lbl) in enumerate([("系统状态", self.lbl_state), ("运行模式", self.lbl_mode),
                                          ("扫码会话", self.lbl_session), ("活动箱数", self.lbl_active),
                                          ("速度估计 mm/s", self.lbl_speed), ("事件队列", self.lbl_queue)]):
            g.addWidget(QLabel(title + "："), i // 3, (i % 3) * 2)
            g.addWidget(lbl, i // 3, (i % 3) * 2 + 1)
        root.addWidget(box)

        dev = QGroupBox("设备 / 光电")
        self.led_row = QHBoxLayout(dev)
        self.led_pe1, self.led_pe2, self.led_dio = Led("PE1"), Led("PE2"), Led("DIO")
        for w in (self.led_pe1, self.led_pe2, self.led_dio):
            self.led_row.addWidget(w)
        self.led_row.addStretch(1)
        root.addWidget(dev)

        self.lbl_counters = QLabel("统计：-")
        root.addWidget(self.lbl_counters)

        self.table = make_table(["时间", "Track", "结果", "码值", "速度", "箱长", "报警", "耗时ms"])
        root.addWidget(self.table, 1)

        self.lbl_alarm = QLabel("")
        self.lbl_alarm.setStyleSheet("color:#c0392b; font-weight:bold;")
        root.addWidget(self.lbl_alarm)

        ctl = QHBoxLayout()
        self.combo_mode = QComboBox()
        self.combo_mode.addItems(["LR", "FB"])
        b_mode, b_clear, b_reset = QPushButton("切换模式(仅待机)"), QPushButton("清除报警"), QPushButton("计数清零")
        b_mode.clicked.connect(lambda: self._cmd("set_mode", mode=self.combo_mode.currentText()))
        b_clear.clicked.connect(lambda: self._cmd("clear_alarms"))
        b_reset.clicked.connect(lambda: self._cmd("reset_counters"))
        for w in (QLabel("模式："), self.combo_mode, b_mode, b_clear, b_reset):
            ctl.addWidget(w)
        ctl.addStretch(1)
        root.addLayout(ctl)

        if controller.simulate:
            sim = QGroupBox("仿真")
            h = QHBoxLayout(sim)
            self.code_edit = QLineEdit("6901234567890")
            h.addWidget(QLabel("码值："))
            h.addWidget(self.code_edit)
            for text, kind in [("过一箱(读到)", "ok"), ("过一箱(读不到)", "noread"),
                               ("单侧读到", "one_side"), ("异码冲突", "conflict")]:
                b = QPushButton(text)
                b.clicked.connect(lambda _=False, k=kind: self._simulate(k))
                h.addWidget(b)
            root.addWidget(sim)
        vm.snapshot_changed.connect(self.on_snapshot)

    def _cmd(self, cmd: str, **kw) -> None:
        c = self.controller.container
        if c is not None:
            c.runtime.submit_command(cmd, **kw)

    def _simulate(self, kind: str) -> None:
        c = self.controller.container
        if c is not None:
            spawn(simulate_box_pass(c, self.code_edit.text().strip() or "6901234567890", kind))

    def _sync_cameras(self, ids: list[str]) -> None:
        if list(self._cam_leds) == ids:
            return
        for led in self._cam_leds.values():
            self.led_row.removeWidget(led)
            led.deleteLater()
        self._cam_leds = {}
        for i, cid in enumerate(ids):
            led = Led(cid)
            self.led_row.insertWidget(i, led)
            self._cam_leds[cid] = led

    def on_snapshot(self, s: dict) -> None:
        st = s["state"]
        self.lbl_state.setText(st)
        self.lbl_state.setStyleSheet(f"color:{_STATE_COLOR.get(st, '#7f8c8d')}; font-weight:bold;")
        self.lbl_mode.setText(s["mode"])
        if not self.combo_mode.hasFocus():
            self.combo_mode.setCurrentText(s["mode"])
        self.lbl_session.setText(s["session"]["state"])
        self.lbl_active.setText(str(s["active_tracks"]))
        self.lbl_speed.setText(_fmt(s["speed_mm_s"]) or "-")
        self.lbl_queue.setText(str(s["queue"]))

        cams = s["cameras"]
        self._sync_cameras([x["id"] for x in cams])
        for x in cams:
            self._cam_leds[x["id"]].set_state(x["online"], f'{x["id"]} {"在线" if x["online"] else "离线"}')
        for led, key in ((self.led_pe1, "pe1"), (self.led_pe2, "pe2")):
            on = bool(s["pe"].get(key))
            led.set_state(None, f'{key.upper()} {"遮挡" if on else "空闲"}', "#e67e22" if on else None)
        dio_on = s["dio"]["online"]
        self.led_dio.set_state(dio_on, f'DIO {"在线" if dio_on else "离线"}')

        c = s["counters"]
        self.lbl_counters.setText("统计：" + "  |  ".join(f"{k} {v}" for k, v in c.items()))
        rows = [[r["time"], r["track_id"], r["status"], r["code"], _fmt(r["speed_mm_s"]),
                 _fmt(r["length_mm"]), ",".join(r["alarms"]), _fmt(r["process_ms"], 1)]
                for r in s["recent"]]
        if rows != self._last_rows:
            self._last_rows = rows
            fill_table(self.table, rows, [status_color(r[2]) for r in rows])
        self.lbl_alarm.setText(f"当前报警：{s['alarm']}" if s["alarm"] else "")
