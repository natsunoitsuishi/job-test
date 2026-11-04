"""日志（README §11.16）。

文件：app.log（全部）/ runtime.log（业务）/ device.log（设备通讯）/ alarm.log（报警）。
格式：2026-03-18 15:12:01.233 | INFO  | runtime | track_id=T... | message
业务日志用 extra={"track_id": ..., "camera_id": ...} 传上下文。
logger 命名：vg.runtime vg.track vg.binder vg.scan vg.decision vg.camera vg.dio vg.report vg.alarm ...
"""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path
from typing import Optional

ROOT_NAME = "vg"
_RUNTIME_TAGS = {"runtime", "track", "binder", "scan", "decision", "scheduler", "app", "health"}
_DEVICE_TAGS = {"camera", "dio", "report", "plc"}
_ALARM_TAGS = {"alarm"}

_FMT = "%(asctime)s.%(msecs)03d | %(levelname)-5s | %(tag)s | %(ctx)s%(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"
_MARK = "_vg_handler"


class _ContextFilter(logging.Filter):
    """补充 tag（logger 名最后一段）与 ctx（track_id=.. | camera_id=.. | ）。"""

    def filter(self, record: logging.LogRecord) -> bool:
        parts = record.name.split(".")
        record.tag = parts[1] if len(parts) > 1 and parts[0] == ROOT_NAME else record.name
        ctx = ""
        track_id = getattr(record, "track_id", None)
        camera_id = getattr(record, "camera_id", None)
        if track_id:
            ctx += f"track_id={track_id} | "
        if camera_id:
            ctx += f"camera_id={camera_id} | "
        record.ctx = ctx
        return True


class _TagFilter(logging.Filter):
    def __init__(self, tags: set[str]) -> None:
        super().__init__()
        self._tags = tags

    def filter(self, record: logging.LogRecord) -> bool:
        return getattr(record, "tag", "") in self._tags


def _to_level(level: str | int) -> int:
    if isinstance(level, int):
        return level
    return getattr(logging, str(level).upper(), logging.INFO)


def setup_logging(level: str | int = "INFO", log_dir: Optional[str | Path] = None,
                  console: bool = True, *, max_bytes: int = 5 * 1024 * 1024,
                  backup_count: int = 5) -> logging.Logger:
    """幂等：重复调用会替换此前安装的 handler。"""
    root = logging.getLogger(ROOT_NAME)
    for h in list(root.handlers):
        if getattr(h, _MARK, False):
            root.removeHandler(h)
            h.close()
    root.setLevel(_to_level(level))
    root.propagate = False

    formatter = logging.Formatter(_FMT, datefmt=_DATEFMT)
    handlers: list[logging.Handler] = []

    if console:
        handlers.append(logging.StreamHandler())

    if log_dir is not None:
        d = Path(log_dir)
        d.mkdir(parents=True, exist_ok=True)
        spec = [("app.log", None), ("runtime.log", _RUNTIME_TAGS),
                ("device.log", _DEVICE_TAGS), ("alarm.log", _ALARM_TAGS)]
        for fname, tags in spec:
            fh = logging.handlers.RotatingFileHandler(
                d / fname, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
            if tags is not None:
                fh.addFilter(_TagFilter(tags))
            handlers.append(fh)

    ctx_filter = _ContextFilter()
    for h in handlers:
        h.setFormatter(formatter)
        # 上下文过滤器必须先于 tag 过滤器执行
        h.filters.insert(0, ctx_filter)
        setattr(h, _MARK, True)
        root.addHandler(h)
    return root


def set_log_level(level: str | int) -> None:
    """热切换日志级别（README §11.14）。"""
    logging.getLogger(ROOT_NAME).setLevel(_to_level(level))


def get_logger(tag: str) -> logging.Logger:
    return logging.getLogger(f"{ROOT_NAME}.{tag}")


def track_extra(track_id: Optional[str] = None, camera_id: Optional[str] = None) -> dict:
    extra: dict = {}
    if track_id:
        extra["track_id"] = track_id
    if camera_id:
        extra["camera_id"] = camera_id
    return {"extra": extra}


