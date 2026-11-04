"""AppController（README §11.9 AppController）：startup / shutdown / restart_runtime。

restart_runtime 会重新读取配置文件并重建全部设备与服务（network / 相机 / DIO 映射等需重启的参数借此生效）。
"""
from __future__ import annotations

import asyncio
import contextlib
import os
from typing import Callable, Mapping, Optional

from app.bootstrap import AppContainer, build_app
from infra.logging import get_logger
from infra.util.clock import SYSTEM_CLOCK, Clock

logger = get_logger("app")


class AppController:
    def __init__(self, config_path: Optional[str | os.PathLike[str]] = None, *,
                 simulate: Optional[bool] = None, clock: Clock = SYSTEM_CLOCK,
                 environ: Optional[Mapping[str, str]] = None) -> None:
        self._config_path = config_path
        self._simulate = simulate
        self._clock = clock
        self._environ = environ
        self.container: Optional[AppContainer] = None
        self._sim_resolved = bool(simulate)
        self._lock = asyncio.Lock()
        self._restart_listeners: list[Callable[[], None]] = []

    @property
    def simulate(self) -> bool:
        return self._sim_resolved

    def add_restart_listener(self, cb: Callable[[], None]) -> None:
        self._restart_listeners.append(cb)

    async def startup(self) -> AppContainer:
        """配置非法抛 ConfigError；设备离线不阻断（进入 FAULT/告警）。"""
        if self.container is not None:
            return self.container
        c = build_app(self._config_path, simulate=self._simulate, clock=self._clock,
                      environ=self._environ)
        try:
            await c.runtime.start()
            await c.health.start()
        except BaseException:
            await self._teardown(c)
            raise
        self._sim_resolved = c.simulate
        self.container = c
        logger.info("应用已启动 simulate=%s", c.simulate)
        return c

    @staticmethod
    async def _teardown(c: AppContainer) -> None:
        with contextlib.suppress(Exception):
            await c.health.stop()
        with contextlib.suppress(Exception):
            await c.runtime.stop()

    async def shutdown(self) -> None:
        c, self.container = self.container, None
        if c is None:
            return
        await self._teardown(c)
        logger.info("应用已停止")

    async def restart_runtime(self) -> None:
        async with self._lock:
            await self.shutdown()
            await self.startup()
            for cb in list(self._restart_listeners):
                try:
                    cb()
                except Exception:  # noqa: BLE001
                    logger.exception("重启监听回调异常")
