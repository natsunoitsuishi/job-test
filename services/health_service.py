"""健康服务（README §11.12 health_check_loop）：设备健康汇总、数据库探活、调度心跳。
设备离线/重连事件由设备层经 EventBus 上报，本服务只做汇总与被动探测，不重复报设备报警。"""
from __future__ import annotations

import asyncio
import contextlib
from typing import Callable, Optional

from config.schema import AppConfig
from devices.camera.service import CameraService
from devices.dio.base import BaseDiDoService
from domain.alarm_service import AlarmService
from domain.enums import AlarmCode
from infra.db.repository import ScanRepository
from infra.logging import get_logger
from infra.util.clock import SYSTEM_CLOCK, Clock
from services.report_service import ReportService

logger = get_logger("health")


class HealthService:
    def __init__(self, cfg: AppConfig, dio: BaseDiDoService, camera: CameraService,
                 repo: ScanRepository, report: ReportService, alarms: AlarmService,
                 status_provider: Callable[[], dict], clock: Clock = SYSTEM_CLOCK, *,
                 interval_s: float = 1.0, db_ping_interval_s: float = 10.0) -> None:
        self.cfg = cfg
        self.dio, self.camera, self.repo = dio, camera, repo
        self.report, self.alarms = report, alarms
        self._status = status_provider
        self._clock = clock
        self._interval_s = interval_s
        self._db_interval_s = db_ping_interval_s
        self._task: Optional[asyncio.Task] = None
        self._last_db = 0.0
        self._last_hb = 0.0
        self.db_ok = True
        self.heartbeat_ok: Optional[bool] = None
        self.checked_ts: Optional[float] = None
        self._devices: list[dict] = []

    def update_config(self, cfg: AppConfig) -> None:
        self.cfg = cfg

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="health_check")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def _loop(self) -> None:
        while True:
            try:
                await self.check_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("健康检查异常")
            await asyncio.sleep(self._interval_s)

    async def check_once(self, now: Optional[float] = None) -> None:
        now = self._clock.now() if now is None else now
        self.checked_ts = now
        healths = [self.dio.get_health(), *self.camera.get_healths()]
        self._devices = [{"device_id": h.device_id, "device_type": h.device_type,
                          "status": h.status.value, "last_heartbeat_ts": h.last_heartbeat_ts,
                          "message": h.message} for h in healths]
        if now - self._last_db >= self._db_interval_s:
            self._last_db = now
            self.db_ok = await self.repo.ping()
            if self.db_ok:
                self.alarms.clear_alarm(AlarmCode.DB_ERROR, "db")
            else:
                self.alarms.raise_alarm(AlarmCode.DB_ERROR, "数据库不可用", "ERROR", key="db")
        sc = self.cfg.report.scheduler
        if sc.enabled and now - self._last_hb >= sc.heartbeat_interval_s:
            self._last_hb = now
            st = self._status()
            self.heartbeat_ok = await self.report.send_heartbeat({
                "device_id": self.cfg.app.device_id, "line_id": self.cfg.app.line_id,
                "state": st.get("state"), "mode": st.get("mode"),
                "active_tracks": st.get("active_tracks"),
                "cameras_online": self.camera.online_ids(), "ts": now})

    def snapshot(self) -> dict:
        return {"devices": list(self._devices), "db_ok": self.db_ok,
                "heartbeat_ok": self.heartbeat_ok, "report": self.report.snapshot(),
                "checked_ts": self.checked_ts}
