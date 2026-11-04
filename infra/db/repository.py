"""SQLite 仓储（README §11.15 ScanRepository）。

- 单连接 + 线程锁，所有公开方法为 async（内部 asyncio.to_thread），不阻塞事件循环。
- 写操作在 BEGIN IMMEDIATE 事务内执行；遇到 OperationalError（如 database is locked）短重试，
  最终失败抛 DbError，由运行时报 DB_ERROR 并重试/缓存（README §11.18）。
- db_path 为 ":memory:" 时使用内存库（测试用）；相对路径相对项目根目录。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import sqlite3
import threading
import time
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any, Callable, Mapping, Optional, Sequence

from config.manager import resolve_path
from domain.models import AppEvent, BoxTrack, CameraResult
from infra.logging import get_logger
from infra.util.clock import iso_ts

from .migrations import migrate

logger = get_logger("runtime")


class DbError(Exception):
    """数据库不可用或写入失败。"""


def _json_default(o: Any) -> Any:
    if is_dataclass(o) and not isinstance(o, type):
        return asdict(o)
    if isinstance(o, Enum):
        return o.value
    return str(o)


def _dumps(o: Any) -> str:
    return json.dumps(o, ensure_ascii=False, default=_json_default, separators=(",", ":"))


def _iso(ts: Optional[float]) -> Optional[str]:
    return iso_ts(ts) if ts is not None else None


def track_summary(track: BoxTrack, device_id: str = "", line_id: str = "") -> dict:
    """BoxTrack → 可 JSON 序列化的摘要（scan_record.result_json / 上报 / 归档共用）。"""
    process_ms: Optional[float] = None
    if track.finalized_ts is not None:
        process_ms = round((track.finalized_ts - track.created_ts) * 1000.0, 1)
    return {
        "device_id": device_id, "line_id": line_id,
        "track_id": track.track_id, "mode": track.mode.value,
        "final_code": track.final_code,
        "status": track.final_status.value if track.final_status else "",
        "created_ts": track.created_ts, "finalized_ts": track.finalized_ts,
        "pe1_on_ts": track.pe1_on_ts, "pe1_off_ts": track.pe1_off_ts,
        "pe2_on_ts": track.pe2_on_ts, "pe2_off_ts": track.pe2_off_ts,
        "speed_mm_s": track.speed_mm_s, "length_mm": track.length_mm,
        "scan_window_start_ts": track.scan_window_start_ts,
        "scan_window_end_ts": track.scan_window_end_ts,
        "scan_close_reason": track.scan_close_reason,
        "gap_violation": track.gap_violation,
        "alarm_codes": list(track.alarm_codes),
        "process_time_ms": process_ms,
        "camera_results": [{"camera_id": r.camera_id, "code": r.code, "success": r.success,
                            "result_ts": r.result_ts, "symbology": r.symbology}
                           for r in track.camera_results],
    }


_SCAN_COLS = ["track_id", "device_id", "line_id", "mode", "code_value", "status", "pe1_ts", "pe2_ts",
              "speed_mm_s", "length_mm", "window_start", "window_end", "close_reason", "alarm_codes",
              "process_time_ms", "result_json", "created_ts", "created_at"]
_SCAN_UPSERT = (f"INSERT INTO scan_record ({','.join(_SCAN_COLS)}) "
                f"VALUES ({','.join(':' + c for c in _SCAN_COLS)}) "
                f"ON CONFLICT(track_id) DO UPDATE SET "
                + ",".join(f"{c}=excluded.{c}" for c in _SCAN_COLS if c != "track_id"))


class ScanRepository:
    def __init__(self, db_path: str = "data/app.db", *, retries: int = 3,
                 retry_delay_s: float = 0.05) -> None:
        self._path = db_path
        self._retries = max(1, retries)
        self._retry_delay_s = retry_delay_s
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ 生命周期
    def _open_sync(self) -> None:
        if self._conn is not None:
            return
        path = self._path
        if path != ":memory:":
            p = resolve_path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            path = str(p)
        conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=5.0)
        conn.row_factory = sqlite3.Row
        if path != ":memory:":
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        migrate(conn)
        self._conn = conn

    async def open(self) -> None:
        try:
            await asyncio.to_thread(self._open_sync)
        except sqlite3.Error as e:
            raise DbError(f"打开数据库失败: {e}") from e

    def _close_sync(self) -> None:
        with self._lock:
            conn, self._conn = self._conn, None
            if conn is not None:
                with contextlib.suppress(sqlite3.Error):
                    conn.close()

    async def close(self) -> None:
        await asyncio.to_thread(self._close_sync)

    @property
    def is_open(self) -> bool:
        return self._conn is not None

    # ------------------------------------------------------------------ 执行框架
    def _run(self, fn: Callable[[sqlite3.Connection], Any], write: bool) -> Any:
        last: Optional[Exception] = None
        for attempt in range(self._retries):
            try:
                with self._lock:
                    conn = self._conn
                    if conn is None:
                        raise DbError("数据库未打开")
                    if not write:
                        return fn(conn)
                    conn.execute("BEGIN IMMEDIATE")
                    try:
                        result = fn(conn)
                        conn.execute("COMMIT")
                        return result
                    except BaseException:
                        with contextlib.suppress(sqlite3.Error):
                            conn.execute("ROLLBACK")
                        raise
            except sqlite3.OperationalError as e:
                last = e
                time.sleep(self._retry_delay_s * (attempt + 1))
            except sqlite3.Error as e:
                raise DbError(f"数据库错误: {e}") from e
        raise DbError(f"数据库操作失败（已重试 {self._retries} 次）: {last}")

    async def _arun(self, fn: Callable[[sqlite3.Connection], Any], write: bool = True) -> Any:
        return await asyncio.to_thread(self._run, fn, write)

    # ------------------------------------------------------------------ 写入：扫码记录
    async def save_scan_record(self, track: BoxTrack, *, device_id: str = "", line_id: str = "") -> None:
        summary = track_summary(track, device_id, line_id)
        row = {
            "track_id": track.track_id, "device_id": device_id, "line_id": line_id,
            "mode": track.mode.value, "code_value": track.final_code,
            "status": summary["status"] or "UNKNOWN",
            "pe1_ts": _iso(track.pe1_on_ts), "pe2_ts": _iso(track.pe2_on_ts),
            "speed_mm_s": track.speed_mm_s, "length_mm": track.length_mm,
            "window_start": _iso(track.scan_window_start_ts), "window_end": _iso(track.scan_window_end_ts),
            "close_reason": track.scan_close_reason, "alarm_codes": ",".join(track.alarm_codes),
            "process_time_ms": summary["process_time_ms"], "result_json": _dumps(summary),
            "created_ts": track.created_ts, "created_at": iso_ts(track.created_ts),
        }
        await self._arun(lambda c: c.execute(_SCAN_UPSERT, row))

    async def save_camera_results(self, results: Sequence[CameraResult], track_id: Optional[str]) -> None:
        if not results:
            return
        rows = [(track_id, r.camera_id, r.code, 1 if r.success else 0, r.symbology, r.result_ts,
                 _dumps(r.raw_payload), iso_ts(r.result_ts)) for r in results]
        await self._arun(lambda c: c.executemany(
            "INSERT INTO camera_result_record "
            "(track_id,camera_id,code_value,success,symbology,result_ts,raw_payload,created_at) "
            "VALUES (?,?,?,?,?,?,?,?)", rows))

    async def save_camera_result(self, result: CameraResult, track_id: Optional[str]) -> None:
        """track_id 为 None 表示 UNBOUND_RESULT（未绑定到任何箱）。"""
        await self.save_camera_results([result], track_id)

    # ------------------------------------------------------------------ 写入：报警 / 事件 / 配置
    async def save_alarm(self, alarm: Any) -> None:
        """alarm 为 domain.alarm_service.Alarm 或其 to_dict() 的结果；按 (code,key,start_ts) 幂等更新。"""
        d: Mapping[str, Any] = alarm.to_dict() if hasattr(alarm, "to_dict") else alarm
        row = {
            "code": d.get("code") or d.get("alarm_code"), "key": d.get("key", "") or "",
            "level": d.get("level") or d.get("alarm_level") or "ERROR",
            "message": d.get("message", ""), "count": int(d.get("count", 1)),
            "start_ts": float(d["start_ts"]), "end_ts": d.get("end_ts"), "ack_user": d.get("ack_user"),
        }
        await self._arun(lambda c: c.execute(
            "INSERT INTO alarm_record (alarm_code,alarm_key,alarm_level,message,count,start_ts,end_ts,ack_user) "
            "VALUES (:code,:key,:level,:message,:count,:start_ts,:end_ts,:ack_user) "
            "ON CONFLICT(alarm_code,alarm_key,start_ts) DO UPDATE SET "
            "message=excluded.message,count=excluded.count,end_ts=excluded.end_ts,"
            "ack_user=excluded.ack_user,alarm_level=excluded.alarm_level", row))

    async def close_stale_alarms(self, ts: float) -> int:
        """启动时把上次运行遗留的未结束报警标记为结束（README §11.19：重启后只恢复历史，不恢复激活态）。"""
        def fn(c: sqlite3.Connection) -> int:
            return c.execute("UPDATE alarm_record SET end_ts=? WHERE end_ts IS NULL", (ts,)).rowcount
        return int(await self._arun(fn))

    async def save_event(self, event: AppEvent) -> None:
        await self._arun(lambda c: c.execute(
            "INSERT INTO system_event_log (event_type,source,payload,ts,created_at) VALUES (?,?,?,?,?)",
            (event.event_type.value, event.source, _dumps(event.payload), event.ts, iso_ts(event.ts))))

    async def save_config_snapshot(self, config: Mapping[str, Any], version: int, ts: Optional[float] = None) -> None:
        t = time.time() if ts is None else ts
        await self._arun(lambda c: c.execute(
            "INSERT INTO config_snapshot (version,config_json,updated_at) VALUES (?,?,?)",
            (int(version), _dumps(config), iso_ts(t))))

    async def last_config_snapshot(self) -> Optional[dict]:
        def fn(c: sqlite3.Connection) -> Optional[dict]:
            r = c.execute("SELECT version,config_json,updated_at FROM config_snapshot "
                          "ORDER BY id DESC LIMIT 1").fetchone()
            if r is None:
                return None
            return {"version": r["version"], "config": json.loads(r["config_json"]),
                    "updated_at": r["updated_at"]}
        return await self._arun(fn, write=False)

    # ------------------------------------------------------------------ 查询
    @staticmethod
    def _where(filters: Optional[Mapping[str, Any]]) -> tuple[str, list[Any]]:
        f = filters or {}
        cond: list[str] = []
        args: list[Any] = []
        if f.get("status"):
            cond.append("status=?"); args.append(str(f["status"]))
        if f.get("mode"):
            cond.append("mode=?"); args.append(str(f["mode"]))
        if f.get("track_id"):
            cond.append("track_id=?"); args.append(str(f["track_id"]))
        if f.get("code"):
            cond.append("code_value LIKE ?"); args.append(f"%{f['code']}%")
        if f.get("start_ts") is not None:
            cond.append("created_ts>=?"); args.append(float(f["start_ts"]))
        if f.get("end_ts") is not None:
            cond.append("created_ts<=?"); args.append(float(f["end_ts"]))
        if f.get("has_alarm"):
            cond.append("alarm_codes<>''")
        return (" WHERE " + " AND ".join(cond)) if cond else "", args

    async def query_scan_records(self, filters: Optional[Mapping[str, Any]] = None, limit: int = 100,
                                 offset: int = 0) -> list[dict]:
        where, args = self._where(filters)
        sql = f"SELECT * FROM scan_record{where} ORDER BY created_ts DESC, id DESC LIMIT ? OFFSET ?"
        def fn(c: sqlite3.Connection) -> list[dict]:
            return [dict(r) for r in c.execute(sql, [*args, int(limit), int(offset)])]
        return await self._arun(fn, write=False)

    async def count_scan_records(self, filters: Optional[Mapping[str, Any]] = None) -> int:
        where, args = self._where(filters)
        def fn(c: sqlite3.Connection) -> int:
            return int(c.execute(f"SELECT COUNT(*) FROM scan_record{where}", args).fetchone()[0])
        return await self._arun(fn, write=False)

    async def latest_scan_records(self, n: int = 100) -> list[dict]:
        return await self.query_scan_records(None, limit=n)

    async def get_camera_results(self, track_id: str) -> list[dict]:
        def fn(c: sqlite3.Connection) -> list[dict]:
            return [dict(r) for r in c.execute(
                "SELECT * FROM camera_result_record WHERE track_id=? ORDER BY result_ts", (track_id,))]
        return await self._arun(fn, write=False)

    async def query_alarms(self, active_only: bool = False, limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM alarm_record" + (" WHERE end_ts IS NULL" if active_only else "") \
              + " ORDER BY start_ts DESC, id DESC LIMIT ?"
        def fn(c: sqlite3.Connection) -> list[dict]:
            return [dict(r) for r in c.execute(sql, (int(limit),))]
        return await self._arun(fn, write=False)

    async def query_events(self, event_type: Optional[str] = None, limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM system_event_log" + (" WHERE event_type=?" if event_type else "") \
              + " ORDER BY ts DESC, id DESC LIMIT ?"
        args: list[Any] = ([event_type] if event_type else []) + [int(limit)]
        def fn(c: sqlite3.Connection) -> list[dict]:
            return [dict(r) for r in c.execute(sql, args)]
        return await self._arun(fn, write=False)

    async def stats(self, since_ts: Optional[float] = None) -> dict[str, int]:
        """{"total": N, "OK": a, "NO_READ": b, ...}；since_ts 为空统计全部。"""
        where, args = ("WHERE created_ts>=?", [since_ts]) if since_ts is not None else ("", [])
        def fn(c: sqlite3.Connection) -> dict[str, int]:
            out = {r["status"]: int(r["n"]) for r in c.execute(
                f"SELECT status, COUNT(*) AS n FROM scan_record {where} GROUP BY status", args)}
            out["total"] = sum(out.values())
            return out
        return await self._arun(fn, write=False)

    # ------------------------------------------------------------------ 上报缓存（README §11.18）
    async def enqueue_report(self, target: str, payload: Mapping[str, Any], max_cache: int = 1000,
                             ts: Optional[float] = None) -> None:
        """加入待重发队列；同 target 超过 max_cache 时丢弃最旧的。"""
        t = time.time() if ts is None else ts
        def fn(c: sqlite3.Connection) -> None:
            c.execute("INSERT INTO report_queue (target,payload,created_ts) VALUES (?,?,?)",
                      (target, _dumps(payload), t))
            c.execute("DELETE FROM report_queue WHERE target=? AND id NOT IN "
                      "(SELECT id FROM report_queue WHERE target=? ORDER BY id DESC LIMIT ?)",
                      (target, target, int(max_cache)))
        await self._arun(fn)

    async def peek_reports(self, target: str, limit: int = 50) -> list[dict]:
        """按入队顺序返回 [{"id", "payload", "attempts", "created_ts"}]，不删除。"""
        def fn(c: sqlite3.Connection) -> list[dict]:
            return [{"id": r["id"], "payload": json.loads(r["payload"]), "attempts": r["attempts"],
                     "created_ts": r["created_ts"]}
                    for r in c.execute("SELECT * FROM report_queue WHERE target=? ORDER BY id LIMIT ?",
                                       (target, int(limit)))]
        return await self._arun(fn, write=False)

    async def ack_reports(self, ids: Sequence[int]) -> None:
        if ids:
            await self._arun(lambda c: c.executemany("DELETE FROM report_queue WHERE id=?",
                                                     [(int(i),) for i in ids]))

    async def fail_reports(self, ids: Sequence[int]) -> None:
        if ids:
            await self._arun(lambda c: c.executemany(
                "UPDATE report_queue SET attempts=attempts+1 WHERE id=?", [(int(i),) for i in ids]))

    async def report_queue_size(self, target: Optional[str] = None) -> int:
        def fn(c: sqlite3.Connection) -> int:
            if target is None:
                return int(c.execute("SELECT COUNT(*) FROM report_queue").fetchone()[0])
            return int(c.execute("SELECT COUNT(*) FROM report_queue WHERE target=?", (target,)).fetchone()[0])
        return await self._arun(fn, write=False)

    # ------------------------------------------------------------------ 维护
    async def purge_before(self, ts: float) -> dict[str, int]:
        """删除 ts 之前的扫码/相机结果/事件日志和已结束的报警，返回各表删除行数。"""
        def fn(c: sqlite3.Connection) -> dict[str, int]:
            return {
                "scan_record": c.execute("DELETE FROM scan_record WHERE created_ts<?", (ts,)).rowcount,
                "camera_result_record": c.execute("DELETE FROM camera_result_record WHERE result_ts<?", (ts,)).rowcount,
                "system_event_log": c.execute("DELETE FROM system_event_log WHERE ts<?", (ts,)).rowcount,
                "alarm_record": c.execute("DELETE FROM alarm_record WHERE end_ts IS NOT NULL AND end_ts<?", (ts,)).rowcount,
            }
        return await self._arun(fn)

    async def ping(self) -> bool:
        try:
            await self._arun(lambda c: c.execute("SELECT 1").fetchone(), write=False)
            return True
        except DbError:
            return False
