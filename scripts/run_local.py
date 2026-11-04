"""无界面仿真运行：python -m scripts.run_local --boxes 20
DIO/相机全部仿真，按比例混入 读不到 / 单侧读到 / 异码（后两者需启用两台相机）。"""
from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.lifecycle import AppController  # noqa: E402
from app.simulation import simulate_box_pass  # noqa: E402
from config import ConfigError  # noqa: E402
from devices.camera import SimulatedCameraClient  # noqa: E402


async def amain(args: argparse.Namespace) -> int:
    env = dict(os.environ)
    env.setdefault("VISION_GATE__STORAGE__DB_PATH", args.db)
    if not args.verbose:
        env.setdefault("VISION_GATE__LOGGING__CONSOLE", "false")
    controller = AppController(args.config, simulate=True, environ=env)
    c = await controller.startup()

    def on_event(kind: str, data) -> None:
        if kind == "track_finalized":
            spd = f"{data['speed_mm_s']:.0f}" if data["speed_mm_s"] else "-"
            print(f"{data['time']}  {data['track_id']}  {data['status']:<9} code={data['code'] or '-':<14} "
                  f"v={spd}mm/s  alarms={','.join(data['alarms']) or '-'}")
        elif kind == "alarm" and data["event"] == "raised":
            a = data["alarm"]
            print(f"  [ALARM] {a['code']} {a['message']}")

    c.runtime.add_listener(on_event)
    sims = [x for x in c.camera.clients if isinstance(x, SimulatedCameraClient)]
    kinds = ["ok"] * 7 + ["noread"] + (["one_side", "conflict"] if len(sims) >= 2 else [])
    rng = random.Random(args.seed)
    for i in range(args.boxes):
        await simulate_box_pass(c, f"69012345{i:05d}", rng.choice(kinds))
        await asyncio.sleep(args.interval)
    await asyncio.sleep(1.5)
    counters = c.runtime.snapshot()["counters"]
    print("统计:", ", ".join(f"{k}={v}" for k, v in counters.items()))
    await controller.shutdown()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="仿真运行（无界面）")
    p.add_argument("--config", default=None)
    p.add_argument("--boxes", type=int, default=10)
    p.add_argument("--interval", type=float, default=0.6, help="箱与箱之间的间隔（秒）")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--db", default="data/demo.db")
    p.add_argument("--verbose", action="store_true", help="日志同时输出到控制台")
    args = p.parse_args()
    try:
        return asyncio.run(amain(args))
    except ConfigError as e:
        print(f"配置错误:\n{e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
