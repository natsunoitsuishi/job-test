"""触发调度（README §4.5 / §11.9 TriggerScheduler）：计算每箱读码窗口，管理开/关。

窗口：
  start = max(pe2_on, 箱头到达最近读码面 - window_lead)
  end   = min(各候选)：
            max_window     start + max_track_window(+相机偏移差)      —— 开窗即有
            predicted_leave 箱尾离开读码面 + margin（PE1 落下后用实测箱长）
            tail_delay     箱尾离开读码面 + tail_delay（PE1 落下后）
            first_ok       首个有效码 + track_hold_after_ok
  PE1 落下之前箱长未知，只受 max_window 约束；end 不早于 start + track_hold_after_ok。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from config.schema import AppConfig
from infra.logging import get_logger, track_extra

from .enums import RunMode, TrackStatus
from .models import BoxTrack, CameraTriggerPlan
from .track_manager import TrackManager

logger = get_logger("scheduler")


class SpeedEstimator:
    def __init__(self, sensor_distance_mm: float, nominal_mm_s: float, tolerance: float) -> None:
        self.sensor_distance_mm = sensor_distance_mm
        self.nominal_mm_s = nominal_mm_s
        self.tolerance = tolerance

    def is_plausible(self, v: float) -> bool:
        return abs(v - self.nominal_mm_s) <= self.nominal_mm_s * self.tolerance

    def calc(self, pe1_on_ts: Optional[float], pe2_on_ts: Optional[float]) -> float:
        """PE1→PE2 实测速度；缺失/异常时回退标称线速。"""
        if pe1_on_ts is None or pe2_on_ts is None:
            return self.nominal_mm_s
        dt = pe2_on_ts - pe1_on_ts
        if dt <= 0:
            return self.nominal_mm_s
        v = self.sensor_distance_mm / dt
        return v if self.is_plausible(v) else self.nominal_mm_s


@dataclass
class _WindowPlan:
    start: float
    candidates: dict[str, float] = field(default_factory=dict)


class TriggerScheduler:
    def __init__(self, cfg: AppConfig, tracks: TrackManager) -> None:
        self._tracks = tracks
        self._plans: dict[str, _WindowPlan] = {}
        self.update_config(cfg)

    def update_config(self, cfg: AppConfig) -> None:
        self._t = cfg.trigger
        self._rt = cfg.runtime
        self._offsets = {c.id: c.pe2_offset_mm for c in cfg.camera.enabled}
        self._min_off = min(self._offsets.values())
        self._max_off = max(self._offsets.values())
        self.speed = SpeedEstimator(self._t.sensor_distance_mm, self._rt.line_speed_mm_s,
                                    self._rt.speed_tolerance)

    # ------------------------------------------------------------------ 内部
    def _v(self, track: BoxTrack) -> float:
        return track.speed_mm_s or self._rt.line_speed_mm_s

    def _recompute_end(self, track: BoxTrack) -> None:
        plan = self._plans[track.track_id]
        floor = plan.start + self._t.track_hold_after_ok_ms / 1000.0
        reason, end = min(plan.candidates.items(), key=lambda kv: kv[1])
        end = max(end, floor)
        track.scan_window_end_ts = end
        track.scan_close_reason = reason

    def _tighten(self, track: BoxTrack, reason: str, ts: float) -> None:
        plan = self._plans.get(track.track_id)
        if plan is None or track.status is not TrackStatus.WINDOW_OPEN:
            return
        plan.candidates[reason] = ts
        self._recompute_end(track)

    # ------------------------------------------------------------------ 开窗
    def open_scan_window(self, track: BoxTrack, mode: Optional[RunMode] = None) -> BoxTrack:
        if track.pe2_on_ts is None:
            raise ValueError("PE2 未匹配，不能开窗")
        v = self.speed.calc(track.pe1_on_ts, track.pe2_on_ts)
        track.speed_mm_s = v
        head_arrival = track.pe2_on_ts + self._min_off / v
        start = max(track.pe2_on_ts, head_arrival - self._t.window_lead_ms / 1000.0)
        cap = start + self._t.max_track_window_ms / 1000.0 + (self._max_off - self._min_off) / v
        self._plans[track.track_id] = _WindowPlan(start=start, candidates={"max_window": cap})
        track.scan_window_start_ts = start
        track.status = TrackStatus.WINDOW_OPEN
        self._recompute_end(track)
        if self._t.scan_mode == "burst_trigger":
            self.build_trigger_plans(track)
        logger.info("window open [%.3f, %.3f] v=%.0fmm/s mode=%s", start, track.scan_window_end_ts, v,
                    (mode or track.mode).value, **track_extra(track.track_id))
        return track

    # ------------------------------------------------------------------ 收窗
    def prepare_window_close(self, track: BoxTrack, pe1_fall_ts: float) -> BoxTrack:
        """PE1 下降沿：得到实测箱长，收紧窗口。"""
        track.pe1_off_ts = pe1_fall_ts
        v = self._v(track)
        length = v * (pe1_fall_ts - (track.pe1_on_ts or pe1_fall_ts))
        track.length_mm = length if length > 0 else self._t.default_box_length_mm
        if track.pe2_on_ts is not None and track.track_id in self._plans:
            tail_leave = track.pe2_on_ts + (self._max_off + track.length_mm) / v
            self._tighten(track, "predicted_leave", tail_leave + self._t.read_zone_margin_mm / v)
            self._tighten(track, "tail_delay", tail_leave + self._t.tail_delay_ms / 1000.0)
        return track

    def note_first_ok(self, track: BoxTrack, ts: float) -> None:
        """首个有效码：window 在 ts + track_hold_after_ok_ms 关闭。"""
        if track.first_ok_ts is None:
            track.first_ok_ts = ts
        self._tighten(track, "first_ok", track.first_ok_ts + self._t.track_hold_after_ok_ms / 1000.0)

    def close_expired_windows(self, now_ts: float) -> list[BoxTrack]:
        closed: list[BoxTrack] = []
        for t in self._tracks.get_active_tracks():
            if t.status is not TrackStatus.WINDOW_OPEN or t.scan_window_end_ts is None:
                continue
            start = t.scan_window_start_ts or now_ts
            if now_ts >= t.scan_window_end_ts:
                pass
            elif not t.camera_results and now_ts >= start + self._t.camera_result_timeout_ms / 1000.0:
                t.scan_window_end_ts = now_ts
                t.scan_close_reason = "result_timeout"
            else:
                continue
            t.status = TrackStatus.WAITING_RESULT
            closed.append(t)
            logger.info("window closed reason=%s", t.scan_close_reason, **track_extra(t.track_id))
        return closed

    def collect_due(self, now_ts: float) -> list[BoxTrack]:
        """窗口已关且 result_grace_ms 已过的轨迹：可以判定。"""
        grace = self._t.result_grace_ms / 1000.0
        return [t for t in self._tracks.get_active_tracks()
                if t.status is TrackStatus.WAITING_RESULT and t.scan_window_end_ts is not None
                and now_ts >= t.scan_window_end_ts + grace]

    def forget(self, track: BoxTrack) -> None:
        self._plans.pop(track.track_id, None)

    # ------------------------------------------------------------------ burst_trigger 降级
    def build_trigger_plans(self, track: BoxTrack) -> list[CameraTriggerPlan]:
        v = self._v(track)
        interval = self._t.burst_interval_ms / 1000.0
        plans: list[CameraTriggerPlan] = []
        for cam_id, off in self._offsets.items():
            arrival = (track.pe2_on_ts or 0.0) + off / v
            first = max(track.scan_window_start_ts or arrival, arrival)
            for k in range(self._t.burst_count):
                ts = first + k * interval
                if track.scan_window_end_ts is None or ts <= track.scan_window_end_ts:
                    plans.append(CameraTriggerPlan(cam_id, ts, off))
        track.trigger_plans = plans
        return plans

    def pop_due_triggers(self, now_ts: float) -> list[tuple[BoxTrack, CameraTriggerPlan]]:
        """到点且未发送的触发计划；已有有效码或窗口已关的轨迹不再触发。"""
        due: list[tuple[BoxTrack, CameraTriggerPlan]] = []
        for t in self._tracks.get_active_tracks():
            if t.status is not TrackStatus.WINDOW_OPEN or t.first_ok_ts is not None:
                continue
            for p in t.trigger_plans:
                if not p.trigger_sent and p.trigger_ts <= now_ts:
                    p.trigger_sent = True
                    due.append((t, p))
        return due
