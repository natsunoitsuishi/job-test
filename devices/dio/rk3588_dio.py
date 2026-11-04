"""RK3588 本机 DI/DO（sysfs GPIO）。gpio_di_pins/gpio_do_pins 按「通道号」索引填写 GPIO 编号。
假设：高电平 = 有效（光电遮挡/输出吸合）；如现场反相，在隔离板侧处理。"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from config.schema import DioSection
from devices.common import EventSink
from infra.logging import get_logger
from infra.util.clock import SYSTEM_CLOCK, Clock

from .base import BaseDiDoService, DioError

logger = get_logger("dio")


class Rk3588DiDoService(BaseDiDoService):
    def __init__(self, cfg: DioSection, emit: EventSink, clock: Clock = SYSTEM_CLOCK, *,
                 sysfs_root: str = "/sys/class/gpio") -> None:
        super().__init__(cfg, emit, clock)
        self._root = Path(sysfs_root)
        self._di_files: dict[int, Path] = {}
        self._do_files: dict[int, Path] = {}

    def _setup_pin(self, pin: int, direction: str) -> Path:
        d = self._root / f"gpio{pin}"
        if not d.exists():
            try:
                (self._root / "export").write_text(str(pin))
            except OSError as e:
                raise DioError(f"导出 GPIO{pin} 失败: {e}") from e
            for _ in range(50):                      # 等待 udev 建好目录与权限
                if (d / "direction").exists():
                    break
                time.sleep(0.02)
        try:
            (d / "direction").write_text(direction)
        except OSError as e:
            raise DioError(f"设置 GPIO{pin} 方向失败: {e}") from e
        return d / "value"

    def _open_sync(self) -> None:
        self._di_files = {ch: self._setup_pin(self.cfg.gpio_di_pins[ch], "in")
                          for ch in self.cfg.di_map.values()}
        self._do_files = {ch: self._setup_pin(self.cfg.gpio_do_pins[ch], "low")
                          for ch in self.cfg.do_map.values()}
        for ch in self._do_files:
            self.do_state[ch] = False

    async def _open(self) -> None:
        await asyncio.to_thread(self._open_sync)

    async def _close(self) -> None:
        for p in self._do_files.values():
            try:
                p.write_text("0")
            except OSError:
                pass

    async def _read_inputs(self) -> dict[int, bool]:
        return {ch: p.read_text().strip() == "1" for ch, p in self._di_files.items()}

    async def _write_output(self, channel: int, value: bool) -> None:
        p = self._do_files.get(channel)
        if p is None:
            raise DioError(f"未配置的输出通道 {channel}")
        p.write_text("1" if value else "0")
