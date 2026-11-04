"""SQLite 表结构（README §11.15）。

时间约定：scan_record 的 pe1_ts/pe2_ts/window_*/created_at 为 ISO 文本（便于人工查看），
其余 *_ts 为 Unix 秒 REAL（便于范围查询与排序）。
report_queue 为 README §11.18 要求的「上报失败本地缓存」，重启后保留。
"""
from __future__ import annotations

SCHEMA_V1: list[str] = [
    """CREATE TABLE IF NOT EXISTS scan_record (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        track_id        TEXT NOT NULL UNIQUE,
        device_id       TEXT NOT NULL DEFAULT '',
        line_id         TEXT NOT NULL DEFAULT '',
        mode            TEXT NOT NULL,
        code_value      TEXT,
        status          TEXT NOT NULL,
        pe1_ts          TEXT,
        pe2_ts          TEXT,
        speed_mm_s      REAL,
        length_mm       REAL,
        window_start    TEXT,
        window_end      TEXT,
        close_reason    TEXT,
        alarm_codes     TEXT NOT NULL DEFAULT '',
        process_time_ms REAL,
        result_json     TEXT NOT NULL DEFAULT '{}',
        created_ts      REAL NOT NULL,
        created_at      TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_scan_created ON scan_record(created_ts)",
    "CREATE INDEX IF NOT EXISTS idx_scan_status  ON scan_record(status)",
    "CREATE INDEX IF NOT EXISTS idx_scan_code    ON scan_record(code_value)",
    """CREATE TABLE IF NOT EXISTS camera_result_record (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        track_id    TEXT,
        camera_id   TEXT NOT NULL,
        code_value  TEXT,
        success     INTEGER NOT NULL,
        symbology   TEXT NOT NULL DEFAULT '',
        result_ts   REAL NOT NULL,
        raw_payload TEXT NOT NULL DEFAULT '{}',
        created_at  TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_cam_track ON camera_result_record(track_id)",
    "CREATE INDEX IF NOT EXISTS idx_cam_ts    ON camera_result_record(result_ts)",
    """CREATE TABLE IF NOT EXISTS alarm_record (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        alarm_code  TEXT NOT NULL,
        alarm_key   TEXT NOT NULL DEFAULT '',
        alarm_level TEXT NOT NULL,
        message     TEXT NOT NULL DEFAULT '',
        count       INTEGER NOT NULL DEFAULT 1,
        start_ts    REAL NOT NULL,
        end_ts      REAL,
        ack_user    TEXT,
        UNIQUE(alarm_code, alarm_key, start_ts)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_alarm_start ON alarm_record(start_ts)",
    """CREATE TABLE IF NOT EXISTS config_snapshot (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        version     INTEGER NOT NULL,
        config_json TEXT NOT NULL,
        updated_at  TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS system_event_log (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT NOT NULL,
        source     TEXT NOT NULL,
        payload    TEXT NOT NULL DEFAULT '{}',
        ts         REAL NOT NULL,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_event_ts ON system_event_log(ts)",
    """CREATE TABLE IF NOT EXISTS report_queue (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        target     TEXT NOT NULL,
        payload    TEXT NOT NULL,
        created_ts REAL NOT NULL,
        attempts   INTEGER NOT NULL DEFAULT 0
    )""",
    "CREATE INDEX IF NOT EXISTS idx_rq_target ON report_queue(target, id)",
]
