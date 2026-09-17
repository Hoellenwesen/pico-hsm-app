#!/usr/bin/env python3
"""
gui/app.py — QApplication-Einstieg der Pico-HSM-GUI.

Start: `pico-hsm-gui` (siehe pyproject.toml) oder `python -m gui.app`.
Dark-Default (getroffene Entscheidung, Schritt 5); Umschalter im
Hauptfenster (Navigations-Eintrag unten).
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication
from qfluentwidgets import Theme, setTheme

from gui.main_window import MainWindow

APPLICATION_NAME = "Pico HSM"


def main(argv: list[str] | None = None) -> int:
    """GUI starten; Rückgabe = QApplication-Exit-Code (für Tests)."""
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APPLICATION_NAME)
    setTheme(Theme.DARK)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
