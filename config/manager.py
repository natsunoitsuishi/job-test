"""配置加载 / 环境变量覆盖 / 受控更新（README §11.14 生效规则、§11.18 配置恢复）。"""
from __future__ import annotations

import copy
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml
from pydantic import ValidationError

from .schema import AppConfig

logger = logging.getLogger("vg.config")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.yaml"
ENV_PREFIX = "VISION_GATE__"

# 修改这些键需要重启运行时（README §11.14 生效规则）
RESTART_REQUIRED_PREFIXES = (
    "network.", "dio.driver", "dio.host", "dio.port", "dio.unit_id", "dio.di_map", "dio.do_map",
    "dio.gpio_", "camera.", "storage.db_path", "report.",
)
STANDBY_ONLY_KEYS = ("runtime.mode",)
HOT_KEYS = ("logging.level",)


class ConfigError(Exception):
    """配置非法；启动阶段抛出会阻断启动。"""


def resolve_path(p: str | os.PathLike[str]) -> Path:
    """相对路径一律相对项目根目录解析。"""
    path = Path(p)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _set_nested(d: dict, keys: list[str], value: Any) -> None:
    cur = d
    for k in keys[:-1]:
        nxt = cur.get(k)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[k] = nxt
        cur = nxt
    cur[keys[-1]] = value


def apply_env_overrides(raw: dict, environ: Optional[Mapping[str, str]] = None) -> dict:
    """VISION_GATE__TRIGGER__TAIL_DELAY_MS=60 → raw['trigger']['tail_delay_ms'] = 60。"""
    env = os.environ if environ is None else environ
    out = copy.deepcopy(raw)
    for k, v in env.items():
        if not k.startswith(ENV_PREFIX):
            continue
        keys = [s.lower() for s in k[len(ENV_PREFIX):].split("__") if s]
        if not keys:
            continue
        try:
            value = yaml.safe_load(v)
        except yaml.YAMLError:
            value = v
        _set_nested(out, keys, value)
    return out


def _read_yaml(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError as e:
        raise ConfigError(f"配置文件不存在: {path}") from e
    except yaml.YAMLError as e:
        raise ConfigError(f"配置文件解析失败 {path}: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"配置文件顶层必须是映射: {path}")
    return data


def validate_config(raw: dict) -> AppConfig:
    try:
        return AppConfig.model_validate(raw)
    except ValidationError as e:
        lines = [f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()]
        raise ConfigError("配置校验失败:\n  " + "\n  ".join(lines)) from e


def load_config(path: Optional[str | os.PathLike[str]] = None,
                environ: Optional[Mapping[str, str]] = None) -> tuple[AppConfig, str]:
    """返回 (配置, 实际使用的文件路径)。非法配置抛 ConfigError。"""
    p = resolve_path(path) if path else DEFAULT_CONFIG_PATH
    raw = apply_env_overrides(_read_yaml(p), environ)
    return validate_config(raw), str(p)


def config_to_dict(cfg: AppConfig) -> dict:
    return cfg.model_dump(mode="json")


@dataclass
class UpdateResult:
    applied: list[str] = field(default_factory=list)
    restart_required: list[str] = field(default_factory=list)
    hot: list[str] = field(default_factory=list)


def _flatten(d: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, Mapping):
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


class ConfigManager:
    """持有当前生效配置；解析失败回退到最后一次有效配置（README §11.18）。"""

    def __init__(self, path: Optional[str | os.PathLike[str]] = None,
                 environ: Optional[Mapping[str, str]] = None) -> None:
        self.path = resolve_path(path) if path else DEFAULT_CONFIG_PATH
        self._environ = environ
        self._cfg: Optional[AppConfig] = None
        self._version = 0

    @property
    def config(self) -> AppConfig:
        if self._cfg is None:
            raise ConfigError("配置尚未加载")
        return self._cfg

    @property
    def version(self) -> int:
        return self._version

    def load(self) -> AppConfig:
        """首次加载：失败直接抛 ConfigError（阻断启动）。"""
        self._cfg, _ = load_config(self.path, self._environ)
        self._version += 1
        return self._cfg

    def reload(self) -> tuple[AppConfig, bool]:
        """重新读盘；失败保留旧配置并返回 (旧配置, False)。"""
        try:
            cfg, _ = load_config(self.path, self._environ)
        except ConfigError as e:
            logger.error("重载配置失败，沿用最后一次有效配置: %s", e)
            return self.config, False
        self._cfg = cfg
        self._version += 1
        return cfg, True

    def update(self, changes: Mapping[str, Any], *, standby: bool = False) -> UpdateResult:
        """changes 形如 {"trigger.tail_delay_ms": 60}。整体校验通过才生效（原子）。"""
        flat_keys = list(changes.keys())
        if not standby:
            for k in flat_keys:
                if k in STANDBY_ONLY_KEYS:
                    raise ConfigError(f"{k} 仅允许待机时修改")
        raw = config_to_dict(self.config)
        for k, v in changes.items():
            _set_nested(raw, k.split("."), v)
        new_cfg = validate_config(raw)
        old_flat = _flatten(config_to_dict(self.config))
        new_flat = _flatten(config_to_dict(new_cfg))
        res = UpdateResult()
        for k in flat_keys:
            if old_flat.get(k) == new_flat.get(k):
                continue
            res.applied.append(k)
            if k in HOT_KEYS:
                res.hot.append(k)
            elif k.startswith(RESTART_REQUIRED_PREFIXES) or k in STANDBY_ONLY_KEYS:
                res.restart_required.append(k)
        self._cfg = new_cfg
        self._version += 1
        return res

    def save(self, path: Optional[str | os.PathLike[str]] = None) -> Path:
        target = resolve_path(path) if path else self.path
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            yaml.safe_dump(config_to_dict(self.config), f, allow_unicode=True, sort_keys=False)
        os.replace(tmp, target)
        return target

