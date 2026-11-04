"""程序入口。

    python -m app.main                 # 现场模式，有 PySide6 时带界面
    python -m app.main --headless      # 无界面（systemd 服务）
    python -m app.main --simulate      # 仿真设备，可配合 --sim-boxes 自动投料
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.lifecycle import AppController  # noqa: E402

logger = logging.getLogger("vg.app")


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="扫码视觉门上位机")
    p.add_argument("--config", help="配置文件路径（默认 config/default.yaml）")
    p.add_argument("--headless", action="store_true", help="不启动界面")
    p.add_argument("--simulate", action="store_true", help="使用仿真 DI/相机")
    p.add_argument("--sim-boxes", type=int, default=0, help="仿真模式下自动投放的箱子数")
    p.add_argument("--sim-gap-mm", type=float, default=150, help="仿真箱距")
    return p.parse_args(argv)


async def _sim_feeder(ctrl: AppController, count: int, gap_mm: float) -> None:
    from scripts.run_local import feed_boxes
    await feed_boxes(ctrl.c, count, gap_mm)


async def run_headless(args: argparse.Namespace) -> int:
    ctrl = AppController(args.config, simulate=args.simulate)
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):   # Windows
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
    try:
        await ctrl.startup()
        if args.simulate and args.sim_boxes:
            asyncio.ensure_future(_sim_feeder(ctrl, args.sim_boxes, args.sim_gap_mm))
        await stop.wait()
    finally:
        await ctrl.shutdown()
    return 0


def run_gui(args: argparse.Namespace) -> int:
    from PySide6.QtWidgets import QApplication
    from qasync import QEventLoop

    from ui.main_window import MainWindow

    qt_app = QApplication(sys.argv)
    loop = QEventLoop(qt_app)
    asyncio.set_event_loop(loop)
    ctrl = AppController(args.config, simulate=args.simulate)
    window_holder = {}

    async def boot() -> None:
        await ctrl.startup()
        w = MainWindow(ctrl.runtime)
        w.closed.connect(lambda: asyncio.ensure_future(quit_app()))
        w.show()
        window_holder["w"] = w
        if args.simulate and args.sim_boxes:
            asyncio.ensure_future(_sim_feeder(ctrl, args.sim_boxes, args.sim_gap_mm))

    async def quit_app() -> None:
        await ctrl.shutdown()
        loop.stop()

    signal.signal(signal.SIGINT, lambda *_: asyncio.ensure_future(quit_app()))
    with loop:
        loop.run_until_complete(boot())
        loop.run_forever()
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    if not args.headless:
        try:
            import PySide6  # noqa: F401
            import qasync  # noqa: F401
        except ImportError:
            print("未安装 PySide6/qasync，以 --headless 模式运行")
            args.headless = True
    if args.headless:
        return asyncio.run(run_headless(args))
    return run_gui(args)


if __name__ == "__main__":
    sys.exit(main())