"""应用生命周期（README §11.9 AppController / §11.10 系统状态机 / §11.19 启停与恢复）。

BOOT → LOAD_CONFIG → INIT_DEVICES → SELF_CHECK → READY → RUNNING ⇄ FAULT → STOPPING

- 配置非法：阻断启动（ConfigError）；
- 设备离线：不阻断启动，进入 FAULT 并持续自动重连，设备恢复后回到 RUNNING；
- 停机：停止收事件 → 停后台任务 → 停扫码会话 → 刷库 → 关设备 → 保存运行快照。
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

from config.manager import config_to_dict, load_config, resolve_path
from config.schema import AppConfig
from domain.enums import AlarmCode, DeviceStatus, SystemState
from infra.logging.setup import setup_logging

from .bootstrap import AppContainer, build_app

logger = logging.getLogger("vg.app")

APP_VERSION = "2.0.0"


class AppController:
    def __init__(self, config_path: Optional[str] = None, *, simulate: bool = False,
                 cfg: Optional[AppConfig] = None) -> None:
        self.config_path = config_path
        self.simulate = simulate
        self._cfg = cfg
        self.state = SystemState.BOOT
        self.c: Optional[AppContainer] = None
        self._stopped = asyncio.Event()
        self._stopping = False

    @property
    def runtime(self):
        return self.c.runtime if self.c else None

    def _set_state(self, s: SystemState) -> None:
        self.state = s
        if self.c:
            self.c.runtime.system_state = s
        logger.info("系统状态 -> %s", s.value)

    # ------------------------------------------------------------------ 启动
    async def startup(self) -> AppContainer:
        self._set_state(SystemState.LOAD_CONFIG)
        if self._cfg is None:
            self._cfg, used = load_config(self.config_path)
        else:
            used = "<内存配置>"
        cfg = self._cfg
        setup_logging(cfg.logging.level, resolve_path(cfg.storage.log_dir), cfg.logging.console)
        logger.info("===== %s 上位机 v%s 启动，配置 %s，%s =====", cfg.app.site_name, APP_VERSION, used,
                    "仿真模式" if self.simulate else "现场模式")

        self.c = c = build_app(cfg, simulate=self.simulate)
        await c.repo.open()
        c.repo.save_config_snapshot(APP_VERSION, config_to_dict(cfg))

        self._set_state(SystemState.INIT_DEVICES)
        await c.runtime.start()           # 先启动消费者，设备事件不会丢
        await c.dio.start()
        for cam in c.cameras.values():
            await cam.start()
        await c.reports.start()
        if c.plc:
            try:
                await c.plc.connect()
            except Exception as e:
                c.alarms.raise_alarm(AlarmCode.DEVICE_OFFLINE + "_PLC", f"PLC 连接失败: {e}")
        await c.archive.start()
        await c.health.start()

        self._set_state(SystemState.SELF_CHECK)
        ok = await self._self_check(cfg.runtime.self_check_timeout_s)
        self._set_state(SystemState.READY)
        self._set_state(SystemState.RUNNING if ok else SystemState.FAULT)
        c.runtime._update_system_state()
        return c

    async def _self_check(self, timeout_s: float) -> bool:
        c = self.c
        devices = [c.dio, *c.cameras.values()]
        deadline = asyncio.get_running_loop().time() + timeout_s
        while asyncio.get_running_loop().time() < deadline:
            if all(d.get_health().status is DeviceStatus.ONLINE for d in devices):
                logger.info("自检通过：DIO 与 %d 台相机在线", len(c.cameras))
                return True
            await asyncio.sleep(0.1)
        offline = [d.get_health().device_id for d in devices if d.get_health().status is not DeviceStatus.ONLINE]
        logger.error("自检未通过，离线设备: %s（将持续自动重连）", offline)
        return False

    # ------------------------------------------------------------------ 停止
    async def shutdown(self) -> None:
        if self._stopping:
            await self._stopped.wait()
            return
        self._stopping = True
        self._set_state(SystemState.STOPPING)
        c = self.c
        if c is None:
            self._stopped.set()
            return
        steps = [
            ("runtime", c.runtime.stop()),
            ("scan_session", c.runtime.session.force_stop()),
            ("health", c.health.stop()),
            ("reports", c.reports.stop()),
            ("plc", c.plc.disconnect() if c.plc else asyncio.sleep(0)),
            ("dio", c.dio.stop()),
            *[(f"camera {cid}", cam.stop()) for cid, cam in c.cameras.items()],
            ("archive", c.archive.stop()),
        ]
        for name, coro in steps:
            try:
                await asyncio.wait_for(coro, 5)
            except Exception as e:
                logger.warning("关闭 %s 异常: %s", name, e)
        self._save_run_snapshot()
        await c.repo.close()
        logger.info("===== 已安全退出 =====")
        self._stopped.set()

    def _save_run_snapshot(self) -> None:
        """保存最近状态，重启后只恢复报警历史与配置，旧轨迹不参与判定（README §11.19）。"""
        try:
            path = resolve_path("data") / "run_snapshot.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            snap = self.c.runtime.snapshot()
            snap["unfinished_tracks"] = [t.summary() for t in self.c.runtime.tracks.get_active_tracks()]
            path.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("保存运行快照失败: %s", e)

    async def wait_stopped(self) -> None:
        await self._stopped.wait()