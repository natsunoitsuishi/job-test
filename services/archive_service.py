"""归档与导出（README §11.22 强烈建议能力：原始报文归档、一键日志打包、CSV 导出）。

- 原始报文：archive_dir/YYYYMMDD/raw.jsonl，每箱一行（含各相机 raw_payload），受 storage.archive_raw_payload 控制；
- 导出：export_dir/scan_<时间>.csv|json（CSV 为 utf-8-sig，Excel 可直接打开）；
- 日志打包：export_dir/logs_<时间>.zip（log_dir 下全部日志 + 可选附加文件）；
- 清理：按天数删除过期的归档目录。
所有文件 IO 放入线程，不阻塞事件循环。
"""
from __future__ import annotations

import asyncio
import csv
import json
import shutil
import threading
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from config.manager import resolve_path
from config.schema import StorageSection
from domain.models import BoxTrack
from infra.db.repository import ScanRepository, track_summary
from infra.logging import get_logger
from infra.util.clock import SYSTEM_CLOCK, Clock

logger = get_logger("runtime")

CSV_COLUMNS = ["created_at", "track_id", "device_id", "line_id", "mode", "status", "code_value",
               "speed_mm_s", "length_mm", "pe1_ts", "pe2_ts", "window_start", "window_end",
               "close_reason", "alarm_codes", "process_time_ms"]


class ArchiveService:
    def __init__(self, cfg: StorageSection, clock: Clock = SYSTEM_CLOCK) -> None:
        self._lock = threading.Lock()
        self._clock = clock
        self.update_config(cfg)

    def update_config(self, cfg: StorageSection) -> None:
        self.cfg = cfg

    # ------------------------------------------------------------------ 路径
    @property
    def archive_dir(self) -> Path:
        return resolve_path(self.cfg.archive_dir)

    @property
    def export_dir(self) -> Path:
        return resolve_path(self.cfg.export_dir)

    def _stamp(self) -> str:
        return datetime.fromtimestamp(self._clock.now()).strftime("%Y%m%d_%H%M%S")

    def _day_dir(self, ts: float) -> Path:
        return self.archive_dir / datetime.fromtimestamp(ts).strftime("%Y%m%d")

    # ------------------------------------------------------------------ 原始报文归档
    def _archive_sync(self, line: str, ts: float) -> Path:
        d = self._day_dir(ts)
        d.mkdir(parents=True, exist_ok=True)
        f = d / "raw.jsonl"
        with self._lock, open(f, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return f

    async def archive_track(self, track: BoxTrack, device_id: str = "", line_id: str = "") -> Optional[Path]:
        """追加一行；未启用或该箱没有任何相机结果时返回 None。失败只记日志，不影响主流程。"""
        if not self.cfg.archive_raw_payload or not track.camera_results:
            return None
        doc = track_summary(track, device_id, line_id)
        doc["camera_results"] = [{"camera_id": r.camera_id, "code": r.code, "success": r.success,
                                  "result_ts": r.result_ts, "symbology": r.symbology,
                                  "raw_payload": r.raw_payload} for r in track.camera_results]
        line = json.dumps(doc, ensure_ascii=False, default=str, separators=(",", ":"))
        try:
            return await asyncio.to_thread(self._archive_sync, line, track.created_ts)
        except OSError as e:
            logger.error("原始报文归档失败: %s", e)
            return None

    # ------------------------------------------------------------------ 导出
    def _target(self, prefix: str, ext: str, path: Optional[str | Path]) -> Path:
        p = resolve_path(path) if path else self.export_dir / f"{prefix}_{self._stamp()}.{ext}"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def _write_csv(self, rows: Iterable[Mapping[str, Any]], target: Path) -> Path:
        tmp = target.with_suffix(target.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in CSV_COLUMNS})
        tmp.replace(target)
        return target

    def _write_json(self, rows: Sequence[Mapping[str, Any]], target: Path) -> Path:
        out = []
        for r in rows:
            d = dict(r)
            raw = d.get("result_json")
            if isinstance(raw, str):
                try:
                    d["result_json"] = json.loads(raw)
                except ValueError:
                    pass
            out.append(d)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        tmp.replace(target)
        return target

    async def export_scan_csv(self, repo: ScanRepository, filters: Optional[Mapping[str, Any]] = None,
                              path: Optional[str | Path] = None, limit: int = 100000) -> Path:
        rows = await repo.query_scan_records(filters, limit=limit)
        target = self._target("scan", "csv", path)
        return await asyncio.to_thread(self._write_csv, rows, target)

    async def export_scan_json(self, repo: ScanRepository, filters: Optional[Mapping[str, Any]] = None,
                               path: Optional[str | Path] = None, limit: int = 100000) -> Path:
        rows = await repo.query_scan_records(filters, limit=limit)
        target = self._target("scan", "json", path)
        return await asyncio.to_thread(self._write_json, rows, target)

    # ------------------------------------------------------------------ 日志打包
    def _zip_logs(self, log_dir: Path, extra: Sequence[Path], target: Path) -> Path:
        tmp = target.with_suffix(".zip.tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            if log_dir.is_dir():
                for f in sorted(log_dir.iterdir()):
                    if f.is_file():
                        zf.write(f, f"logs/{f.name}")
            for f in extra:
                if f.is_file():
                    zf.write(f, f"extra/{f.name}")
        tmp.replace(target)
        return target

    async def package_logs(self, path: Optional[str | Path] = None,
                           extra_files: Sequence[str | Path] = ()) -> Path:
        target = self._target("logs", "zip", path)
        extras = [resolve_path(p) for p in extra_files]
        return await asyncio.to_thread(self._zip_logs, resolve_path(self.cfg.log_dir), extras, target)

    # ------------------------------------------------------------------ 清理
    def _purge_sync(self, keep_days: int, now: float) -> int:
        root = self.archive_dir
        if not root.is_dir():
            return 0
        cutoff = datetime.fromtimestamp(now - keep_days * 86400.0).strftime("%Y%m%d")
        removed = 0
        for d in root.iterdir():
            if d.is_dir() and len(d.name) == 8 and d.name.isdigit() and d.name < cutoff:
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
        return removed

    async def purge_old(self, keep_days: int = 30) -> int:
        """删除早于 keep_days 天的归档目录，返回删除的天数。"""
        n = await asyncio.to_thread(self._purge_sync, keep_days, self._clock.now())
        if n:
            logger.info("已清理 %d 个过期归档目录", n)
        return n

    def list_archive_days(self) -> list[str]:
        root = self.archive_dir
        if not root.is_dir():
            return []
        return sorted(d.name for d in root.iterdir() if d.is_dir() and d.name.isdigit())
