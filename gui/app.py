#!/usr/bin/env python3
"""
gui/app.py — QApplication-Einstieg der Pico-HSM-GUI.

Start: `pico-hsm-gui` (siehe pyproject.toml) oder `python -m gui.app`.
Dark-Default; Umschalter im Hauptfenster (Navigations-Eintrag unten).
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QDialog
from qfluentwidgets import Theme, setTheme

from gui import config as gui_config
from gui.login_dialog import LoginDialog
from gui.main_window import MainWindow
from gui.pin_vault import vault

APPLICATION_NAME = "Pico HSM"


def main(argv: list[str] | None = None) -> int:
    """GUI starten; Rückgabe = QApplication-Exit-Code (für Tests)."""
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APPLICATION_NAME)
    setTheme(Theme.DARK)
    gui_config.load_config()
    dialog = LoginDialog()
    if dialog.exec() != QDialog.Accepted:
        return 0
    result = dialog.result
    if result.serial:
        gui_config.cfg.tokenSerial.value = result.serial
        gui_config.save_config()
    window = MainWindow()
    window.show()
    if result.open_wizard:
        window.show_tab("wizard")
    try:
        return app.exec()
    finally:
        vault.lock()


if __name__ == "__main__":
    raise SystemExit(main())
