from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QVBoxLayout, QWidget)

from domain.enums import DecisionStatus
from ui.widgets.helpers import fill_table, make_table, spawn, status_color


class HistoryPage(QWidget):
    def __init__(self, controller, vm) -> None:
        super().__init__()
        self.controller = controller
        root = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.status = QComboBox()
        self.status.addItems([""] + [s.value for s in DecisionStatus])
        self.code = QLineEdit()
        self.code.setPlaceholderText("码值包含")
        self.track = QLineEdit()
        self.track.setPlaceholderText("Track ID")
        self.has_alarm = QCheckBox("仅含报警")
        b_q, b_csv, b_zip = QPushButton("查询最近100条"), QPushButton("导出CSV"), QPushButton("打包日志")
        b_q.clicked.connect(lambda: spawn(self.query(), self._err))
        b_csv.clicked.connect(lambda: spawn(self.export_csv(), self._err))
        b_zip.clicked.connect(lambda: spawn(self.package(), self._err))
        for w in (QLabel("结果："), self.status, self.code, self.track, self.has_alarm, b_q, b_csv, b_zip):
            bar.addWidget(w)
        bar.addStretch(1)
        root.addLayout(bar)
        self.lbl = QLabel("")
        root.addWidget(self.lbl)
        self.table = make_table(["时间", "Track", "模式", "结果", "码值", "速度", "箱长", "报警", "耗时ms"])
        root.addWidget(self.table, 1)
        vm.rebuilt.connect(lambda: spawn(self.query(), self._err))

    def _filters(self) -> dict:
        return {"status": self.status.currentText(), "code": self.code.text().strip(),
                "track_id": self.track.text().strip(), "has_alarm": self.has_alarm.isChecked()}

    def _err(self, e: BaseException) -> None:
        QMessageBox.critical(self, "操作失败", str(e))

    async def query(self) -> None:
        c = self.controller.container
        if c is None:
            return
        f = self._filters()
        rows = await c.repo.query_scan_records(f, limit=100)
        total = await c.repo.count_scan_records(f)
        self.lbl.setText(f"共 {total} 条，显示最近 {len(rows)} 条")

        def n(v, d=0):
            return "" if v is None else f"{v:.{d}f}"
        data = [[r["created_at"], r["track_id"], r["mode"], r["status"], r["code_value"] or "",
                 n(r["speed_mm_s"]), n(r["length_mm"]), r["alarm_codes"], n(r["process_time_ms"], 1)]
                for r in rows]
        fill_table(self.table, data, [status_color(r[3]) for r in data])

    async def export_csv(self) -> None:
        c = self.controller.container
        if c is not None:
            p = await c.archive.export_scan_csv(c.repo, self._filters())
            QMessageBox.information(self, "导出", f"已导出：{p}")

    async def package(self) -> None:
        c = self.controller.container
        if c is not None:
            p = await c.archive.package_logs()
            QMessageBox.information(self, "日志打包", f"已生成：{p}")
