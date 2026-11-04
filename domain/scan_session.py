"""扫码会话控制（README §11.9 ScanSessionController / §11.11）。

会话是共享资源：活动轨迹 0→1 时启动；活动轨迹回到 0 且空闲超过 idle_off_delay_ms 后停止。
连续来料时不为每箱启停相机。burst_trigger 降级模式下不启停相机（manage_camera=False），
只维护会话状态供 UI 显示。
"""
from __future__ import annotations

import asyncio
from enum import Enum
from typing import Awaitable, Callable, Optional

from infra.logging import get_logger
from infra.util.clock import SYSTEM_CLOCK, Clock

logger = get_logger("scan")


class SessionState(str, Enum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"


AsyncCall = Callable[[], Awaitable[None]]


class ScanSessionController:
    def __init__(self, start_cb: AsyncCall, stop_cb: AsyncCall, active_count: Callable[[], int], *,
                 idle_off_delay_ms: int = 150, trigger_timeout_ms: int = 300,
                 manage_camera: bool = True, clock: Clock = SYSTEM_CLOCK) -> None:
        self._start_cb = start_cb
        self._stop_cb = stop_cb
        self._active_count = active_count
        self._idle_off_s = idle_off_delay_ms / 1000.0
        self._timeout_s = trigger_timeout_ms / 1000.0
        self._manage = manage_camera
        self._clock = clock
        self._state = SessionState.STOPPED
        self._idle_since: Optional[float] = None
        self._lock = asyncio.Lock()
        self.starts = 0
        self.stops = 0
        self.start_failures = 0
        self.last_start_ts: Optional[float] = None
        self.last_stop_ts: Optional[float] = None
        self.last_error = ""

    def update_config(self, *, idle_off_delay_ms: int, trigger_timeout_ms: int, manage_camera: bool) -> None:
        self._idle_off_s = idle_off_delay_ms / 1000.0
        self._timeout_s = trigger_timeout_ms / 1000.0
        self._manage = manage_camera

    @property
    def state(self) -> SessionState:
        return self._state

    def is_running(self) -> bool:
        return self._state is SessionState.RUNNING

    async def ensure_running(self) -> bool:
        """保证会话运行；启动失败/超时返回 False（运行时据此报 CAM_TIMEOUT）。"""
        async with self._lock:
            self._idle_since = None
            if self._state is SessionState.RUNNING:
                return True
            self._state = SessionState.STARTING
            if self._manage:
                try:
                    await asyncio.wait_for(self._start_cb(), self._timeout_s)
                except Exception as e:  # noqa: BLE001
                    self._state = SessionState.STOPPED
                    self.start_failures += 1
                    self.last_error = f"{type(e).__name__}: {e}"
                    logger.error("扫码会话启动失败: %s", self.last_error)
                    return False
            self._state = SessionState.RUNNING
            self.starts += 1
            self.last_start_ts = self._clock.now()
            self.last_error = ""
            logger.info("扫码会话启动")
            return True

    async def stop_if_idle(self, now_ts: Optional[float] = None) -> bool:
        """由运行时 tick 周期调用；满足空闲条件才停止，返回是否真的停止了。"""
        async with self._lock:
            if self._state is not SessionState.RUNNING:
                return False
            if self._active_count() > 0:
                self._idle_since = None
                return False
            now = self._clock.now() if now_ts is None else now_ts
            if self._idle_since is None:
                self._idle_since = now
            if now - self._idle_since < self._idle_off_s:
                return False
            await self._do_stop(now)
            return True

    async def force_stop(self) -> None:
        async with self._lock:
            if self._state is SessionState.STOPPED:
                return
            await self._do_stop(self._clock.now())

    async def _do_stop(self, now: float) -> None:
        self._state = SessionState.STOPPING
        if self._manage:
            try:
                await asyncio.wait_for(self._stop_cb(), self._timeout_s)
            except Exception as e:  # noqa: BLE001 停止失败也视为已停，避免卡死状态机
                self.last_error = f"{type(e).__name__}: {e}"
                logger.error("扫码会话停止异常: %s", self.last_error)
        self._state = SessionState.STOPPED
        self._idle_since = None
        self.stops += 1
        self.last_stop_ts = now
        logger.info("扫码会话停止")

    def snapshot(self) -> dict:
        return {"state": self._state.value, "starts": self.starts, "stops": self.stops,
                "start_failures": self.start_failures, "last_error": self.last_error,
                "last_start_ts": self.last_start_ts, "last_stop_ts": self.last_stop_ts}


