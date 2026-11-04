"""仿真过箱：驱动 SimulatedDiDoService 产生一箱的 PE 边沿，并在读码窗口内让仿真相机“看到”码。

kind：ok 全部相机读到 / noread 全部读不到 / one_side 仅第一台读到 / conflict 第二台读到不同码。
时序：PE2 之后约 (最小相机偏移 / 线速) 箱头到达读码面，窗口提前 window_lead 打开，
在箱头到达前 50 ms 起让码可见约 80 ms（约 2 帧），避免窗口关闭后还有多余结果。
"""
from __future__ import annotations

import asyncio
from typing import Optional

from app.bootstrap import AppContainer
from devices.camera import SimulatedCameraClient

KINDS = ("ok", "noread", "one_side", "conflict")


async def simulate_box_pass(c: AppContainer, code: str = "6901234567890", kind: str = "ok",
                            length_mm: Optional[float] = None) -> None:
    if not hasattr(c.dio, "simulate_box"):
        raise RuntimeError("当前不是仿真模式，无法仿真过箱")
    if kind not in KINDS:
        raise ValueError(f"未知仿真类型: {kind}")
    cfg = c.runtime.cfg
    d = cfg.trigger.sensor_distance_mm
    v = cfg.runtime.line_speed_mm_s
    off = min(cam.pe2_offset_mm for cam in cfg.camera.enabled)
    length = length_mm if length_mm is not None else cfg.trigger.default_box_length_mm
    sims = [cam for cam in c.camera.clients if isinstance(cam, SimulatedCameraClient)]

    box = asyncio.ensure_future(c.dio.simulate_box(length, v, d))     # type: ignore[attr-defined]
    try:
        await asyncio.sleep(max(0.0, (d + off) / v - 0.05))
        if kind == "noread":
            for cam in sims:
                cam.inject(None)
        else:
            for i, cam in enumerate(sims):
                if kind == "one_side" and i > 0:
                    continue
                cam.visible_code = code + "X" if (kind == "conflict" and i == 1) else code
            await asyncio.sleep(0.08)
    finally:
        for cam in sims:
            cam.visible_code = None
    await box
