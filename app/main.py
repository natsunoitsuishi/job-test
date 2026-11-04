"""入口。GUI：python -m app.main；无界面：python -m app.main --headless；
仿真：加 --simulate（DIO 与相机均走仿真，不改配置文件）。"""
from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.lifecycle import AppController  # noqa: E402
from config import ConfigError  # noqa: E402


def _parse(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="扫码视觉门上位机")
    p.add_argument("--config", default=None, help="配置文件路径（默认 config/default.yaml）")
    p.add_argument("--simulate", action="store_true", help="使用 DIO/相机仿真")
    p.add_argument("--headless", action="store_true", help="不启动界面（systemd 守护方式）")
    return p.parse_args(argv)


async def run_headless(args: argparse.Namespace) -> None:
    controller = AppController(args.config, simulate=True if args.simulate else None)
    await controller.startup()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass
    try:
        await stop.wait()
    finally:
        await controller.shutdown()


def run_gui(args: argparse.Namespace) -> int:
    try:
        import qasync
        from PySide6.QtWidgets import QApplication, QMessageBox
    except ImportError as e:
        print(f"缺少界面依赖（{e}）。请安装 PySide6、qasync，或使用 --headless。", file=sys.stderr)
        return 3
    from ui.main_window import MainWindow

    qt_app = QApplication(sys.argv[:1])
    loop = qasync.QEventLoop(qt_app)
    asyncio.set_event_loop(loop)
    controller = AppController(args.config, simulate=True if args.simulate else None)
    code = 0
    with loop:
        try:
            loop.run_until_complete(controller.startup())
        except ConfigError as e:
            QMessageBox.critical(None, "配置错误，已阻断启动", str(e))
            return 2
        window = MainWindow(controller)
        window.show()
        closed = asyncio.Event()
        qt_app.aboutToQuit.connect(closed.set)
        loop.run_until_complete(closed.wait())
        loop.run_until_complete(controller.shutdown())
    return code


def main(argv=None) -> int:
    args = _parse(argv)
    try:
        if args.headless:
            asyncio.run(run_headless(args))
            return 0
        return run_gui(args)
    except ConfigError as e:
        print(f"配置错误，已阻断启动:\n{e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
