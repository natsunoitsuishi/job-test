"""BoxTrack 轨迹管理（README §7.3 / §7.6 / §11.9 TrackManager）。

规则：
- 活动轨迹按创建顺序升序（FIFO）；
- PE2 匹配"最早进入、尚未触发 PE2"的轨迹；
- 箱距过小/时序异常：建轨时标记 gap_violation 并附加 TRACK_OVERLAP / SENSOR_SEQUENCE_ERROR，
  由运行时据此报警（本类不直接依赖 AlarmService）。
"""
from __future__ import annotations

from collections import deque
from typing import Optional

from config.schema import AppConfig
from infra.logging import get_logger, track_extra
from infra.util.clock import make_track_id

from .enums import AlarmCode, DecisionStatus, RunMode, TrackStatus
from .models import BoxTrack

logger = get_logger("track")


class TrackManager:
    def __init__(self, cfg: AppConfig, history_size: int = 200) -> None:
        self.update_config(cfg)
        self._active: list[BoxTrack] = []
        self._recent: deque[BoxTrack] = deque(maxlen=history_size)
        self._by_id: dict[str, BoxTrack] = {}
        self._last_track: Optional[BoxTrack] = None
        self._last_id_ms = -1
        self._seq = 0

    def update_config(self, cfg: AppConfig) -> None:
        self._trigger = cfg.trigger
        self._runtime = cfg.runtime

    # ------------------------------------------------------------------ 建轨
    def _next_id(self, ts: float) -> str:
        ms = int(ts * 1000)
        if ms == self._last_id_ms:
            self._seq += 1
        else:
            self._last_id_ms, self._seq = ms, 0
        return make_track_id(ts, self._seq)

    def create_track(self, ts: float, mode: RunMode) -> BoxTrack:
        track = BoxTrack(track_id=self._next_id(ts), mode=mode, created_ts=ts, pe1_on_ts=ts)
        prev = self._last_track
        if prev is not None:
            self._check_gap(prev, track)
        self._active.append(track)
        self._by_id[track.track_id] = track
        self._last_track = track
        logger.info("created track", **track_extra(track.track_id))
        return track

    def _check_gap(self, prev: BoxTrack, cur: BoxTrack) -> None:
        speed = prev.speed_mm_s or self._runtime.line_speed_mm_s
        if prev.pe1_off_ts is None:
            # 前箱仍遮挡 PE1 时又出现上升沿：边沿丢失或箱距为 0
            cur.add_alarm(AlarmCode.SENSOR_SEQUENCE_ERROR.value)
            self._mark_overlap(prev, cur)
            return
        gap_mm = (cur.pe1_on_ts - prev.pe1_off_ts) * speed
        if gap_mm < 0:
            cur.add_alarm(AlarmCode.SENSOR_SEQUENCE_ERROR.value)
            self._mark_overlap(prev, cur)
        elif gap_mm < self._trigger.min_box_gap_mm:
            self._mark_overlap(prev, cur)

    @staticmethod
    def _mark_overlap(prev: BoxTrack, cur: BoxTrack) -> None:
        cur.gap_violation = True
        cur.add_alarm(AlarmCode.TRACK_OVERLAP.value)
        if not prev.status.is_terminal:
            prev.gap_violation = True
            prev.add_alarm(AlarmCode.TRACK_OVERLAP.value)

    # ------------------------------------------------------------------ 查询
    def get_active_tracks(self) -> list[BoxTrack]:
        return list(self._active)

    @property
    def active_count(self) -> int:
        return len(self._active)

    def get_track(self, track_id: str) -> Optional[BoxTrack]:
        return self._by_id.get(track_id)

    def recent(self, n: int = 10) -> list[BoxTrack]:
        return list(self._recent)[-n:]

    # ------------------------------------------------------------------ 光电匹配
    def match_track_for_pe2(self, ts: float) -> Optional[BoxTrack]:
        """PE2 上升沿：最早进入且尚未触发 PE2 的轨迹；没有则返回 None（时序异常）。"""
        for t in self._active:
            if t.pe2_on_ts is None and t.status is TrackStatus.CREATED and t.pe1_on_ts is not None \
                    and ts >= t.pe1_on_ts:
                return t
        return None

    def attach_pe2(self, track: BoxTrack, ts: float) -> BoxTrack:
        track.pe2_on_ts = ts
        track.status = TrackStatus.TRACKING
        return track

    def match_last_open_track(self) -> Optional[BoxTrack]:
        """PE1 下降沿：最近一个 PE1 仍未落下的轨迹。"""
        for t in reversed(self._active):
            if t.pe1_on_ts is not None and t.pe1_off_ts is None:
                return t
        return None

    def match_track_for_pe2_fall(self) -> Optional[BoxTrack]:
        for t in self._active:
            if t.pe2_on_ts is not None and t.pe2_off_ts is None:
                return t
        return None

    # ------------------------------------------------------------------ 结束/回收
    def finalize_track(self, track_id: str, status: DecisionStatus, ts: Optional[float] = None) -> BoxTrack:
        track = self._by_id[track_id]
        track.final_status = status
        track.status = TrackStatus.FINALIZED
        track.finalized_ts = ts if ts is not None else track.finalized_ts
        self._release(track)
        return track

    def _release(self, track: BoxTrack) -> None:
        if track in self._active:
            self._active.remove(track)
            self._recent.append(track)
        # _by_id 保留最近记录，随 recent 滚动清理
        if len(self._by_id) > self._recent.maxlen + len(self._active) + 50:
            keep = {t.track_id for t in self._recent} | {t.track_id for t in self._active}
            self._by_id = {k: v for k, v in self._by_id.items() if k in keep}

    def cleanup_expired(self, now_ts: float) -> list[BoxTrack]:
        """超过 track_ttl_ms 仍未结束的轨迹：标 EXPIRED/TIMEOUT 并回收，由运行时输出 NG。"""
        ttl_s = self._runtime.track_ttl_ms / 1000.0
        expired: list[BoxTrack] = []
        for t in list(self._active):
            if now_ts - t.created_ts >= ttl_s:
                t.status = TrackStatus.EXPIRED
                if t.final_status is None:
                    t.final_status = DecisionStatus.TIMEOUT
                if t.scan_close_reason is None:
                    t.scan_close_reason = "ttl"
                t.finalized_ts = now_ts
                self._release(t)
                expired.append(t)
                logger.warning("track expired", **track_extra(t.track_id))
        return expired

    def reset(self) -> None:
        """重启后旧轨迹不参与判定（README §11.19）。"""
        self._active.clear()
        self._last_track = None
