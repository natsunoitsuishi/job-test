"""结果绑定（README §7.4 / §7.5 / §11.9 ResultBinder）。

绑定优先级：
  1. result_ts 落入窗口（含 window_tolerance）的轨迹为候选；
  2. 唯一候选 → 直接绑定；
  3. 多个候选 → 取 result_ts 距窗口中心最近者；
  4. 距离并列 → AMBIGUOUS；
  5. 无候选 → UNBOUND_RESULT。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from config.schema import AppConfig

from .enums import DecisionStatus, TrackStatus
from .models import BoxTrack, CameraResult

_TIE_EPS_S = 0.001
_BINDABLE = (TrackStatus.WINDOW_OPEN, TrackStatus.WAITING_RESULT)


@dataclass
class BindOutcome:
    track: Optional[BoxTrack]
    reason: str                      # unique / nearest / ambiguous / unbound
    candidates: list[BoxTrack] = field(default_factory=list)


class ResultBinder:
    def __init__(self, cfg: AppConfig) -> None:
        self.update_config(cfg)

    def update_config(self, cfg: AppConfig) -> None:
        self._t = cfg.trigger
        self._expected_cameras = {c.id for c in cfg.camera.enabled}

    def bind_detailed(self, result: CameraResult, active_tracks: list[BoxTrack]) -> BindOutcome:
        tol = self._t.window_tolerance_ms / 1000.0
        cands = [t for t in active_tracks
                 if t.status in _BINDABLE and t.window_contains(result.result_ts, tol)]
        if not cands:
            return BindOutcome(None, "unbound")
        if len(cands) == 1:
            return BindOutcome(cands[0], "unique", cands)
        ranked = sorted(cands, key=lambda t: abs(result.result_ts - t.window_center(result.result_ts)))
        d0 = abs(result.result_ts - ranked[0].window_center(result.result_ts))
        d1 = abs(result.result_ts - ranked[1].window_center(result.result_ts))
        if d1 - d0 < _TIE_EPS_S:
            return BindOutcome(None, "ambiguous", cands)
        return BindOutcome(ranked[0], "nearest", cands)

    def bind(self, result: CameraResult, active_tracks: list[BoxTrack]) -> Optional[BoxTrack]:
        return self.bind_detailed(result, active_tracks).track

    def resolve_final_code(self, track: BoxTrack) -> tuple[Optional[str], DecisionStatus]:
        """按已绑定结果得出 (最终码, 状态)；状态只反映码的一致性与相机覆盖，不含设备故障/箱距策略。"""
        valid = sorted(track.valid_results, key=lambda r: r.result_ts)
        if not valid:
            if not track.camera_results and track.scan_close_reason in ("result_timeout", "ttl"):
                return None, DecisionStatus.TIMEOUT
            return None, DecisionStatus.NO_READ
        codes: list[str] = []
        for r in valid:
            if r.code not in codes:
                codes.append(r.code)  # type: ignore[arg-type]
        if len(codes) > 1 and self._t.ambiguity_policy == "ng":
            return None, DecisionStatus.AMBIGUOUS
        code = codes[0]
        readers = {r.camera_id for r in valid if r.code == code}
        if len(self._expected_cameras) > 1 and len(readers) < len(self._expected_cameras) \
                and not self._t.allow_single_camera_ok:
            return None, DecisionStatus.NO_READ
        return code, DecisionStatus.OK


