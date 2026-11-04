"""状态视图模型：定时从 RuntimeService / HealthService 取只读快照，经信号推给各页面。
界面不直接接触数据库或设备驱动（README §11.13）。"""
from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal

from infra.logging import get_logger

logger = get_logger("app")


class StateViewModel(QObject):
    snapshot_changed = Signal(object)
    health_changed = Signal(object)
    rebuilt = Signal()

    def __init__(self, controller, interval_ms: int = 200, parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._n = 0
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        controller.add_restart_listener(lambda: self.rebuilt.emit())

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        c = self.controller.container
        if c is None:
            return
        try:
            self.snapshot_changed.emit(c.runtime.snapshot())
            self._n += 1
            if self._n % 5 == 1:
                self.health_changed.emit(c.health.snapshot())
        except Exception:  # noqa: BLE001
            logger.exception("刷新界面状态失败")
