from __future__ import annotations

import json
from typing import Any

import yaml
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from config.manager import (HOT_KEYS, RESTART_REQUIRED_PREFIXES, STANDBY_ONLY_KEYS, ConfigError)
from ui.widgets.helpers import make_table, spawn


def effect_of(key: str) -> str:
    if key in HOT_KEYS:
        return "热切换"
    if key in STANDBY_ONLY_KEYS:
        return "仅待机"
    if key.startswith(RESTART_REQUIRED_PREFIXES):
        return "需重启"
    return "在线可调"


def to_text(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return v if v != "" and yaml.safe_load(v) == v else json.dumps(v, ensure_ascii=False)
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def from_text(text: str) -> Any:
    return "" if text.strip() == "" else yaml.safe_load(text)


class ConfigPage(QWidget):
    def __init__(self, controller, vm) -> None:
        super().__init__()
        self.controller = controller
        self._orig: dict[str, Any] = {}
        root = QVBoxLayout(self)
        root.addWidget(QLabel("双击“值”列修改；字符串留空请填 \"\"。窗口参数可在线调整，保存请在空闲时进行。"))
        self.table = make_table(["参数", "值", "生效方式"], editable=True)
        root.addWidget(self.table, 1)
        row = QHBoxLayout()
        for text, fn in [("应用修改", self.apply), ("保存到文件", self.save), ("重新载入", self.reload_file),
                         ("导入…", self.import_file), ("导出…", self.export_file),
                         ("重启运行时", self.restart)]:
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, f=fn: f())
            row.addWidget(b)
        row.addStretch(1)
        root.addLayout(row)
        self.reload_view()
        vm.rebuilt.connect(self.reload_view)

    def _svc(self):
        c = self.controller.container
        return None if c is None else c.config_service

    def reload_view(self) -> None:
        svc = self._svc()
        if svc is None:
            return
        self._orig = svc.flat()
        keys = sorted(self._orig)
        self.table.setRowCount(len(keys))
        for r, k in enumerate(keys):
            for col, text in enumerate((k, to_text(self._orig[k]), effect_of(k))):
                item = QTableWidgetItem(text)
                if col != 1:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(r, col, item)

    def _collect(self) -> dict[str, Any]:
        changes: dict[str, Any] = {}
        for r in range(self.table.rowCount()):
            key = self.table.item(r, 0).text()
            text = self.table.item(r, 1).text()
            try:
                new = from_text(text)
            except yaml.YAMLError as e:
                raise ConfigError(f"{key} 的值无法解析: {e}") from e
            if new != self._orig.get(key):
                changes[key] = new
        return changes

    def _error(self, e: BaseException) -> None:
        QMessageBox.critical(self, "操作失败", str(e))

    def apply(self) -> None:
        svc = self._svc()
        if svc is None:
            return
        try:
            changes = self._collect()
        except ConfigError as e:
            self._error(e)
            return
        if not changes:
            QMessageBox.information(self, "配置", "没有修改")
            return
        text = "\n".join(f"{k}: {self._orig.get(k)} → {v}" for k, v in changes.items())
        if QMessageBox.question(self, "确认修改运行参数", text) != QMessageBox.StandardButton.Yes:
            return

        async def go() -> None:
            try:
                res = await svc.update(changes)
            except ConfigError as e:
                self._error(e)
                return
            self.reload_view()
            msg = f"已应用 {len(res.applied)} 项。"
            if res.restart_required:
                msg += "\n以下参数需点击“重启运行时”后生效：\n" + "\n".join(res.restart_required)
            QMessageBox.information(self, "配置", msg)

        spawn(go(), self._error)

    def save(self) -> None:
        svc = self._svc()
        if svc is None:
            return
        try:
            p = svc.save()
        except ConfigError as e:
            self._error(e)
            return
        QMessageBox.information(self, "配置", f"已保存到 {p}（旧文件备份为 .bak）")

    def reload_file(self) -> None:
        svc = self._svc()
        if svc is None:
            return

        async def go() -> None:
            ok = await svc.reload()
            self.reload_view()
            QMessageBox.information(self, "配置", "已重新载入" if ok else "文件无效，沿用最后一次有效配置")

        spawn(go(), self._error)

    def import_file(self) -> None:
        svc = self._svc()
        path, _ = QFileDialog.getOpenFileName(self, "导入配置", "", "YAML (*.yaml *.yml)")
        if not path or svc is None:
            return

        async def go() -> None:
            try:
                res = await svc.import_config(path)
            except ConfigError as e:
                self._error(e)
                return
            self.reload_view()
            QMessageBox.information(self, "配置", f"已导入，变更 {len(res.applied)} 项；"
                                    f"需重启生效: {res.restart_required or '无'}")

        spawn(go(), self._error)

    def export_file(self) -> None:
        svc = self._svc()
        path, _ = QFileDialog.getSaveFileName(self, "导出配置", "vision_gate_config.yaml", "YAML (*.yaml)")
        if path and svc is not None:
            svc.export_config(path)
            QMessageBox.information(self, "配置", f"已导出到 {path}")

    def restart(self) -> None:
        if QMessageBox.question(self, "重启运行时", "将停止并重建全部设备与服务，期间不处理来料。继续？") \
                != QMessageBox.StandardButton.Yes:
            return
        spawn(self.controller.restart_runtime(), self._error)
