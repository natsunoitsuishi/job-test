from __future__ import annotations

from PySide6.QtWidgets import QGroupBox, QLabel, QVBoxLayout, QWidget

from infra.util.clock import fmt_ts
from ui.widgets.helpers import fill_table, make_table


def _wrap(text: str = "-") -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    return lbl


class DevicePage(QWidget):
    def __init__(self, controller, vm) -> None:
        super().__init__()
        self.controller = controller
        root = QVBoxLayout(self)
        self.cam_table = make_table(["相机", "在线", "会话", "最近心跳", "结果数", "成功", "失败",
                                     "解析错误", "重连", "信息"])
        g1 = QGroupBox("相机")
        QVBoxLayout(g1).addWidget(self.cam_table)
        root.addWidget(g1, 1)
        self.lbl_dio, self.lbl_session, self.lbl_health = _wrap(), _wrap(), _wrap()
        for title, lbl in (("DI/DO", self.lbl_dio), ("扫码会话", self.lbl_session),
                           ("数据库 / 上报", self.lbl_health)):
            g = QGroupBox(title)
            QVBoxLayout(g).addWidget(lbl)
            root.addWidget(g)
        vm.snapshot_changed.connect(self.on_snapshot)
        vm.health_changed.connect(self.on_health)

    def on_snapshot(self, s: dict) -> None:
        rows = [[x["id"], "是" if x["online"] else "否", "运行" if x["session_running"] else "停止",
                 fmt_ts(x["last_heartbeat_ts"]) if x["last_heartbeat_ts"] else "",
                 x["results"], x["ok"], x["fail"], x["parse_errors"], x["reconnects"], x["message"]]
                for x in s["cameras"]]
        fill_table(self.cam_table, rows, [None if x["online"] else "#fadbd8" for x in s["cameras"]])
        d = s["dio"]
        di = " ".join(f"{k.upper()}={'1' if v else '0'}" for k, v in d["di"].items())
        do = " ".join(f"{k.upper()}={'1' if v else '0'}" for k, v in d["do"].items())
        self.lbl_dio.setText(f"驱动 {d['driver']} | {'在线' if d['online'] else '离线'} | DI {di} | DO {do}"
                             f" | 卡死 {d['stuck'] or '无'} | {d['message']}")
        ss = s["session"]
        self.lbl_session.setText(
            f"状态 {ss['state']} | 启动 {ss['starts']} 次 | 停止 {ss['stops']} 次 | 启动失败 {ss['start_failures']} 次"
            f" | 最近错误 {ss['last_error'] or '无'}")

    def on_health(self, h: dict) -> None:
        r = h["report"]
        hb = {None: "未启用", True: "正常", False: "失败"}[h["heartbeat_ok"]]
        self.lbl_health.setText(
            f"数据库 {'正常' if h['db_ok'] else '异常'} | 调度心跳 {hb} | 上报成功 {r['sent']} 失败 {r['failed']}"
            f" | 待补发 {r['pending']} | 已启用 {r['enabled'] or '无'}")
