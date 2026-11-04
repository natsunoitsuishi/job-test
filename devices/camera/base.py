"""相机客户端基类（README §11.9 CameraClient）。

supervisor 任务负责：连接 → 心跳 → 读取循环 → 断线上报 → 自动重连 → 重连后若会话仍需运行则自动补发 start。
子类实现：connect/disconnect/fetch_loop/_send_start/_send_stop/_send_trigger。
"""
from __future__ import annotations

import asyncio
import contextlib
from abc import ABC, abstractmethod
from typing import Optional

from config.schema import CameraConfig
from devices.common import DeviceError, EventSink, fault_event, recover_event
from domain.enums import DeviceStatus, EventType
from domain.events import make_event
from domain.models import CameraResult, DeviceHealth
from infra.logging import get_logger
from infra.util.clock import SYSTEM_CLOCK, Clock

logger = get_logger("camera")


class CameraError(DeviceError):
    pass


class BaseCameraClient(ABC):
    def __init__(self, cfg: CameraConfig, emit: EventSink, clock: Clock = SYSTEM_CLOCK,
                 bind_host: str = "") -> None:
        self.cfg = cfg
        self.id = cfg.id
        self._emit = emit
        self._clock = clock
        self.bind_host = bind_host
        self.online = False
        self.session_wanted = False
        self.session_running = False
        self.last_heartbeat_ts: Optional[float] = None
        self.message = ""
        self.stats = {"results": 0, "ok": 0, "fail": 0, "parse_errors": 0, "reconnects": 0}
        self._fault_reported = False
        self._task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------ 子类实现
    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def fetch_loop(self) -> None:
        """持续接收结果并调用 self.publish_result；连接关闭时抛异常或返回。"""

    @abstractmethod
    async def _send_start(self) -> None: ...

    @abstractmethod
    async def _send_stop(self) -> None: ...

    @abstractmethod
    async def _send_trigger(self, track_id: str) -> None: ...

    # ------------------------------------------------------------------ 会话指令
    async def start_scan_session(self) -> None:
        self.session_wanted = True          # 失败（如离线）也保留，重连后自动补发
        await self._send_start()
        self.session_running = True

    async def stop_scan_session(self) -> None:
        self.session_wanted = False
        try:
            await self._send_stop()
        finally:
            self.session_running = False

    async def trigger_once(self, track_id: str) -> None:
        await self._send_trigger(track_id)

    # ------------------------------------------------------------------ 结果出口
    def publish_result(self, result: CameraResult, recv_ts: Optional[float] = None) -> None:
        self.stats["results"] += 1
        self.stats["ok" if result.success else "fail"] += 1
        ts = self._clock.now() if recv_ts is None else recv_ts
        self._emit(make_event(EventType.CAMERA_RESULT, self.id, {"result": result}, ts))

    # ------------------------------------------------------------------ 监督
    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._supervise(), name=f"camera_{self.id}")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await self._safe_disconnect()
        self.online = False
        self.session_running = False

    async def _safe_disconnect(self) -> None:
        with contextlib.suppress(Exception):
            await self.disconnect()

    def _mark_online(self) -> None:
        if self.online:
            return
        self.online = True
        self.message = ""
        self.last_heartbeat_ts = self._clock.now()
        logger.info("相机在线", extra={"camera_id": self.id})
        if self._fault_reported:
            self._fault_reported = False
            self.stats["reconnects"] += 1
            self._emit(recover_event(self.id, "camera", ts=self._clock.now()))

    def _mark_offline(self, message: str) -> None:
        self.online = False
        self.session_running = False
        self.message = message
        if not self._fault_reported:
            self._fault_reported = True
            logger.error("相机离线: %s", message, extra={"camera_id": self.id})
            self._emit(fault_event(self.id, "camera", message, ts=self._clock.now()))

    async def _heartbeat_loop(self) -> None:
        interval = max(0.5, min(self.cfg.heartbeat_timeout_s / 2.0, 5.0))
        while True:
            now = self._clock.now()
            self.last_heartbeat_ts = now
            self._emit(make_event(EventType.CAMERA_HEARTBEAT, self.id, {"camera_id": self.id}, now))
            await asyncio.sleep(interval)

    async def _supervise(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self.connect(), self.cfg.connect_timeout_s)
            except Exception as e:  # noqa: BLE001
                await self._safe_disconnect()
                self._mark_offline(f"连接失败: {type(e).__name__}: {e}")
                await asyncio.sleep(self.cfg.reconnect_interval_s)
                continue
            self._mark_online()
            hb = asyncio.create_task(self._heartbeat_loop())
            err = "连接被关闭"
            try:
                if self.session_wanted:
                    try:
                        await self.start_scan_session()
                        logger.info("重连后恢复扫码会话", extra={"camera_id": self.id})
                    except Exception as e:  # noqa: BLE001
                        logger.warning("重连后恢复会话失败: %s", e, extra={"camera_id": self.id})
                await self.fetch_loop()
            except Exception as e:  # noqa: BLE001
                err = f"{type(e).__name__}: {e}"
            finally:
                hb.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await hb
            await self._safe_disconnect()
            self._mark_offline(err)
            await asyncio.sleep(self.cfg.reconnect_interval_s)

    # ------------------------------------------------------------------ 状态
    def get_health(self) -> DeviceHealth:
        return DeviceHealth(self.id, "camera",
                            DeviceStatus.ONLINE if self.online else DeviceStatus.OFFLINE,
                            self.last_heartbeat_ts, self.message)

    def snapshot(self) -> dict:
        return {"id": self.id, "online": self.online, "session_running": self.session_running,
                "message": self.message, "last_heartbeat_ts": self.last_heartbeat_ts, **self.stats}
