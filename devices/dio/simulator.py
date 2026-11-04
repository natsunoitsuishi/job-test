"""DI/DO 仿真：set_input 直接改输入电平；simulate_box 按线速/箱长/PE 间距生成一箱的四个边沿。"""
from __future__ import annotations

import asyncio
from typing import Union

from config.schema import DioSection
from devices.common import EventSink
from infra.util.clock import SYSTEM_CLOCK, Clock

from .base import BaseDiDoService, DioError


class SimulatedDiDoService(BaseDiDoService):
    def __init__(self, cfg: DioSection, emit: EventSink, clock: Clock = SYSTEM_CLOCK, *,
                 sensor_distance_mm: float = 120.0) -> None:
        super().__init__(cfg, emit, clock)
        self.sensor_distance_mm = sensor_distance_mm
        self._inputs: dict[int, bool] = {ch: False for ch in cfg.di_map.values()}
        self._forced_offline = False

    # ------------------------------------------------------------------ 驱动实现
    async def _open(self) -> None:
        if self._forced_offline:
            raise DioError("模拟离线")

    async def _close(self) -> None:
        return None

    async def _read_inputs(self) -> dict[int, bool]:
        if self._forced_offline:
            raise ConnectionError("模拟离线")
        return dict(self._inputs)

    async def _write_output(self, channel: int, value: bool) -> None:
        if self._forced_offline:
            raise ConnectionError("模拟离线")

    # ------------------------------------------------------------------ 仿真控制
    def set_offline(self, flag: bool) -> None:
        self._forced_offline = flag

    def set_input(self, key: Union[str, int], value: bool) -> None:
        ch = self.cfg.di_map[key] if isinstance(key, str) else key
        self._inputs[ch] = bool(value)

    async def simulate_box(self, length_mm: float = 350.0, speed_mm_s: float = 800.0,
                           sensor_distance_mm: float | None = None, time_scale: float = 1.0) -> None:
        """实时模拟一箱通过：PE1↑ → PE2↑(d/v) → PE1↓(L/v) → PE2↓((d+L)/v)。time_scale>1 放慢。"""
        d = self.sensor_distance_mm if sensor_distance_mm is None else sensor_distance_mm
        v = speed_mm_s
        events = sorted([(0.0, "pe1", True), (d / v, "pe2", True),
                         (length_mm / v, "pe1", False), ((d + length_mm) / v, "pe2", False)],
                        key=lambda e: e[0])
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        for t, name, val in events:
            delay = t0 + t * time_scale - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            self.set_input(name, val)
