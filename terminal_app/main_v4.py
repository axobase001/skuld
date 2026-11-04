"""Run the V4-owned Pi Terminal. No old Mimir runtime is instantiated."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtCore import QTimer, QUrl
from PySide6.QtQuick import QQuickWindow

from .qt_bridge_v4 import TerminalBridge
from .secret_loader import load_deepseek_key
from .v4_wiring import V4Terminal
from .accounts import account_paths


def main(argv=None):
    parser = argparse.ArgumentParser(description="Skuld V4 × Pi desktop Terminal")
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).parents[1] / ".terminal-runtime")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--profile", default="default")
    parser.add_argument("--credential-from-env", action="store_true")
    parser.add_argument("--screenshot", type=Path, help="Capture the live UI and exit")
    args = parser.parse_args(argv)
    if args.screenshot:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    repository = Path(__file__).parents[1]
    if args.profile == "default":
        account = account_paths(repository, "default", args.workspace)
        root, workspace = args.data_dir, account.workspace
    else:
        account = account_paths(repository, args.profile)
        account.workspace.mkdir(parents=True, exist_ok=True)
        root, workspace = account.runtime, account.workspace
    credential = load_deepseek_key(repository) if args.credential_from_env else None
    terminal = V4Terminal(root, workspace, credential, args.profile)
    app = QGuiApplication(sys.argv[:1])
    app.setApplicationName("Skuld V4 Terminal")
    app.aboutToQuit.connect(terminal.close)
    bridge = TerminalBridge(terminal)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("terminalBridge", bridge)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name("qml") / "MainV4.qml")))
    if not engine.rootObjects():
        terminal.close()
        return 2
    if args.screenshot:
        def capture():
            destination = args.screenshot.resolve()
            destination.parent.mkdir(parents=True, exist_ok=True)
            image = QQuickWindow.grabWindow(engine.rootObjects()[0])
            app.exit(0 if not image.isNull() and image.save(str(destination)) else 3)
        QTimer.singleShot(1500, capture)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
