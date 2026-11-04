"""配置服务（README §11.13「所有参数修改经 ConfigService」/ §11.14 生效规则）。

- update：整体校验通过才生效；运行参数热更新，network/相机/DIO 映射只改配置、标记“需重启”；
  runtime.mode 仅待机（无活动轨迹）时允许，并通过运行时指令切换；
- save：有活动轨迹时拒绝（窗口参数应空闲时保存），写盘前备份旧文件为 .bak；
- reload：重新读盘，解析失败沿用最后一次有效配置；import/export 参数文件。
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Optional

from config.manager import (ConfigError, ConfigManager, UpdateResult, config_to_dict, load_config,
                            resolve_path)
from infra.db.repository import DbError, ScanRepository
from infra.logging import get_logger, set_log_level

if TYPE_CHECKING:
    from services.health_service import HealthService
    from services.runtime_service import RuntimeService

logger = get_logger("app")


def flatten(d: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, Mapping):
            out.update(flatten(v, key + "."))
        else:
            out[key] = v
    return out


class ConfigService:
    def __init__(self, manager: ConfigManager, runtime: "RuntimeService", health: "HealthService",
                 repo: ScanRepository) -> None:
        self._m = manager
        self._rt = runtime
        self._health = health
        self._repo = repo

    @property
    def config(self):
        return self._m.config

    @property
    def version(self) -> int:
        return self._m.version

    @property
    def path(self) -> Path:
        return self._m.path

    def current_dict(self) -> dict:
        return config_to_dict(self._m.config)

    def flat(self) -> dict[str, Any]:
        return flatten(self.current_dict())

    def is_idle(self) -> bool:
        return self._rt.tracks.active_count == 0

    # ------------------------------------------------------------------ 变更
    async def _apply(self, res: Optional[UpdateResult] = None) -> None:
        cfg = self._m.config
        self._rt.apply_config(cfg)
        self._health.update_config(cfg)
        if res is None or "logging.level" in res.hot:
            set_log_level(cfg.logging.level)
        if res is None or "runtime.mode" in res.applied:
            if cfg.runtime.mode is not self._rt.mode:
                self._rt.submit_command("set_mode", mode=cfg.runtime.mode.value)
        try:
            await self._repo.save_config_snapshot(self.current_dict(), self._m.version)
        except DbError as e:
            logger.warning("配置快照写入失败: %s", e)

    async def update(self, changes: Mapping[str, Any]) -> UpdateResult:
        """校验失败或违反生效规则抛 ConfigError（配置保持不变）。"""
        res = self._m.update(changes, standby=self.is_idle())
        res.restart_required = [k for k in res.restart_required if k != "runtime.mode"]
        await self._apply(res)
        logger.info("配置已更新: %s", ", ".join(res.applied) or "无变化")
        return res

    async def reload(self) -> bool:
        _, ok = self._m.reload()
        if ok:
            await self._apply()
        return ok

    def save(self) -> Path:
        if not self.is_idle():
            raise ConfigError("有活动轨迹，请空闲时再保存配置")
        target = self._m.path
        if target.exists():
            shutil.copy2(target, target.with_name(target.name + ".bak"))
        return self._m.save()

    def export_config(self, path: str | Path) -> Path:
        return self._m.save(path)

    async def import_config(self, path: str | Path) -> UpdateResult:
        new_cfg, _ = load_config(resolve_path(path), environ={})
        new_flat, old_flat = flatten(config_to_dict(new_cfg)), self.flat()
        changes = {k: v for k, v in new_flat.items() if old_flat.get(k) != v}
        return await self.update(changes)
