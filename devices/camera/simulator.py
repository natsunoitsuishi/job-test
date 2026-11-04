"""仿真相机：会话运行期间，只要 visible_code 不为 None，就以 fps 的频率推送该码（模拟箱子在视野内）。"""
from __future__ import annotations

import asyncio
import contextlib
import random
from typing import Optional

from config.schema import CameraConfig
from devices.common import EventSink
from domain.models import CameraResult
from infra.util.clock import SYSTEM_CLOCK, Clock

from .base import BaseCameraClient, CameraError


class SimulatedCameraClient(BaseCameraClient):
    def __init__(self, cfg: CameraConfig, emit: EventSink, clock: Clock = SYSTEM_CLOCK,
                 bind_host: str = "", *, fps: float = 30.0, read_probability: float = 1.0,
                 seed: Optional[int] = None) -> None:
        super().__init__(cfg, emit, clock, bind_host)
        self.visible_code: Optional[str] = None
        self.symbology = "QR"
        self.fps = fps
        self.read_probability = read_probability
        self.triggers: list[str] = []
        self.commands: list[str] = []
        self._rng = random.Random(seed)
        self._forced_offline = False
        self._gen: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------ 仿真控制
    def set_offline(self, flag: bool) -> None:
        self._forced_offline = flag

    def inject(self, code: Optional[str], *, success: Optional[bool] = None, ts: Optional[float] = None) -> CameraResult:
        """立即推送一条结果（不检查会话状态）。code 为 None 或 success=False 表示未读到。"""
        ok = (code is not None) if success is None else success
        now = self._clock.now() if ts is None else ts
        res = CameraResult(self.id, code if ok else None,
                           {"code": code or "NoRead", "simulated": True},
                           now - self.cfg.result_latency_ms / 1000.0, ok, self.symbology if ok else "")
        self.publish_result(res, now)
        return res

    # ------------------------------------------------------------------ 驱动实现
    async def connect(self) -> None:
        if self._forced_offline:
            raise CameraError("模拟离线")

    async def disconnect(self) -> None:
        self._stop_gen()

    async def fetch_loop(self) -> None:
        while True:
            await asyncio.sleep(0.05)
            if self._forced_offline:
                raise CameraError("模拟断线")

    async def _send_start(self) -> None:
        if not self.online:
            raise CameraError("相机离线")
        self.commands.append("start")
        if self._gen is None or self._gen.done():
            self._gen = asyncio.create_task(self._gen_loop())

    async def _send_stop(self) -> None:
        self.commands.append("stop")
        self._stop_gen()

    async def _send_trigger(self, track_id: str) -> None:
        if not self.online:
            raise CameraError("相机离线")
        self.triggers.append(track_id)
        if self.visible_code is not None and self._rng.random() < self.read_probability:
            self.inject(self.visible_code)
        else:
            self.inject(None)

    def _stop_gen(self) -> None:
        g, self._gen = self._gen, None
        if g is not None:
            g.cancel()

    async def _gen_loop(self) -> None:
        period = 1.0 / self.fps
        while True:
            await asyncio.sleep(period)
            code = self.visible_code
            if code is not None and self._rng.random() < self.read_probability:
                self.inject(code)

    async def stop(self) -> None:
        self._stop_gen()
        await super().stop()
