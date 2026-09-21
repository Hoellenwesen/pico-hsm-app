"""
test_gui_skeleton.py — GUI-Skeleton-Tests ohne Display und ohne Hardware.

Läuft immer im Offscreen-Modus (QT_QPA_PLATFORM=offscreen als Default
am Modulstart — deterministisch, kein Display nötig). Deckt ab:
Fenster-Aufbau, Sidebar-Navigation (9 Bereiche: Start-Wizard, 7 CLI-Gruppen + Logs),
Dark-Default + Umschalter, Worker-Signale, Dialog-Helfer.

Was HIER NICHT getestet wird: echte Core-Verdrahtung der Tabs
(jeweiliges Tab-Testmodul) und Darstellung auf realem Display.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThreadPool, QTimer
from PySide6.QtWidgets import QWidget
from qfluentwidgets import MessageBox, Theme, isDarkTheme, setTheme

from gui import workers
from gui.main_window import NAV_ITEMS, MainWindow
from gui.session_helpers import confirm_destructive, show_conflict

EXPECTED_ORDER = ["wizard", "status", "setup", "pin", "dkek", "keys", "backup", "firmware", "logs"]
EXPECTED_TITLES = {
    "wizard": "Start",
    "status": "Status",
    "setup": "Setup",
    "pin": "PIN",
    "dkek": "DKEK",
    "keys": "Schlüssel",
    "backup": "Backup",
    "firmware": "Firmware",
    "logs": "Logs",
}


# --- Aufbau / Navigation -----------------------------------------------------

def test_nav_items_cover_all_cli_groups_in_order():
    routes = [route for route, _, _, _ in NAV_ITEMS]
    assert routes == EXPECTED_ORDER


def test_main_window_builds_with_all_tabs(qapp):
    window = MainWindow()
    try:
        assert window.windowTitle() == "Pico HSM"
        assert window.stackedWidget.count() == len(EXPECTED_ORDER)
        for route in EXPECTED_ORDER:
            tab = window.tab(route)
            assert tab.objectName() == route
            assert window.stackedWidget.indexOf(tab) >= 0
    finally:
        window.close()


def test_nav_titles_are_german(qapp):
    texts = [text for _, _, _, text in NAV_ITEMS]
    assert texts == [EXPECTED_TITLES[r] for r in EXPECTED_ORDER]


def test_sidebar_switch_changes_page(qtbot, monkeypatch):
    """Verdrahtung Nav-Signal -> Seitenwechsel je Route.

    Bewusst per Signal (clicked.emit), nicht per synthetischem Mausklick:
    QFluentWidgets schluckt mouseReleaseEvent auf dem Tree-Widget (geht ans
    innere itemWidget; kein setCurrentItem — das setzt nur den
    Selektionsstatus, schaltet die Seite NICHT um). Das Klick-Rendering ist
    Library-Verantwortung; hier wird geprüft, dass jedes Nav-Item auf die
    richtige Seite schaltet.

    refresh() aller Tabs ist gemockt: Echte Refreshs würden Hardware-
    Zugriffe auslösen — darunter native PC/SC-Calls (setup-Tab), die auf
    Maschinen ohne Smartcard-Dienst hart fehlschlagen (F7). Echte
    Refreshs prüfen die jeweiligen Tab-Testmodule.
    """
    from gui.tabs import backup_tab, dkek_tab, firmware_tab, keys_tab
    from gui.tabs import logs_tab, pin_tab, setup_tab, status_tab, wizard_tab

    calls: list = []

    def _recorder(self):
        calls.append(self.objectName())

    for module in (
        status_tab, setup_tab, pin_tab, dkek_tab, keys_tab,
        backup_tab, firmware_tab, logs_tab, wizard_tab,
    ):
        for attr in dir(module):
            cls = getattr(module, attr)
            if (
                isinstance(cls, type)
                and cls.__name__.endswith("Tab")
                and callable(getattr(cls, "refresh", None))
            ):
                monkeypatch.setattr(cls, "refresh", _recorder)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    try:
        # Zuerst weg vom Start-Tab (wizard): sonst löst dessen Klick
        # keinen Seitenwechsel (kein refresh) aus.
        window.navigationInterface.widget("status").clicked.emit(True)
        qtbot.waitUntil(
            lambda: window.stackedWidget.currentWidget() is window.tab("status"),
            timeout=3000,
        )
        for route in EXPECTED_ORDER:
            nav_widget = window.navigationInterface.widget(route)
            assert nav_widget is not None, route
            nav_widget.clicked.emit(True)
            qtbot.waitUntil(
                lambda r=route: window.stackedWidget.currentWidget()
                is window.tab(r),
                timeout=3000,
            )
        # Zurück auf status: löst dessen refresh() aus — danach hat jeder
        # Tab mit refresh()-Methode mindestens einen Refresh gesehen
        # (MainWindow-Muster; Tabs ohne refresh wie Firmware sind
        # ausgenommen — reines Anzeige-Update nur per Button).
        window.navigationInterface.widget("status").clicked.emit(True)
        qtbot.waitUntil(
            lambda: window.stackedWidget.currentWidget() is window.tab("status"),
            timeout=3000,
        )
        expected = {
            route for route in EXPECTED_ORDER
            if callable(getattr(window.tab(route), "refresh", None))
        }
        assert set(calls) == expected
    finally:
        window.close()


def test_theme_toggle_item_switches_theme_without_page_change(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    try:
        setTheme(Theme.DARK)
        nav_widget = window.navigationInterface.widget("theme")
        assert nav_widget is not None
        current_before = window.stackedWidget.currentWidget()
        nav_widget.clicked.emit(True)
        qtbot.waitUntil(lambda: not isDarkTheme(), timeout=3000)
        assert window.stackedWidget.currentWidget() is current_before
    finally:
        setTheme(Theme.DARK)
        window.close()


def test_no_placeholder_hints_remain(qapp):
    """Kein Bau-Schritt-Hinweis mehr in der UI (alle Tabs ausgebaut)."""
    window = MainWindow()
    try:
        for route in EXPECTED_ORDER:
            labels = window.tab(route).findChildren(QWidget)
            texts = " ".join(
                _text_of(w) for w in labels
            )
            assert "Schritt 6" not in texts, route
    finally:
        window.close()


def _text_of(widget: QWidget) -> str:
    """Text eines Widgets — robust gegen .text als Methode ODER Property."""
    text = getattr(widget, "text", "")
    try:
        value = text() if callable(text) else text
    except Exception:  # noqa: BLE001 — Test-Helfer, nie fehlschlagen
        return ""
    return value if isinstance(value, str) else ""


# --- Theme ---------------------------------------------------------------------

def test_dark_is_default_and_toggle_works(qapp):
    setTheme(Theme.DARK)
    window = MainWindow()
    try:
        assert window.is_dark is True
        window._toggle_theme()
        assert window.is_dark is False
        window._toggle_theme()
        assert window.is_dark is True
    finally:
        setTheme(Theme.DARK)
        window.close()


# --- Worker ----------------------------------------------------------------------

def test_worker_delivers_result(qtbot):
    worker = workers.FunctionWorker(lambda x, y=0: x + y, 20, y=22)
    with qtbot.waitSignal(worker.signals.finished, timeout=5000) as blocker:
        QThreadPool.globalInstance().start(worker)
    assert blocker.args == [42]


def test_worker_delivers_error(qtbot):
    def boom():
        raise RuntimeError("kaput")

    worker = workers.FunctionWorker(boom)
    with qtbot.waitSignal(worker.signals.error, timeout=5000) as blocker:
        QThreadPool.globalInstance().start(worker)
    assert isinstance(blocker.args[0], RuntimeError)


# --- Dialoge -----------------------------------------------------------------------

def _click_dialog_button_later(parent: QWidget, button_name: str) -> None:
    """Dialog-Button per Timer klicken.

    Bewusst NICHT über QApplication.activeModalWidget(): Das meldet den
    QFluentWidgets-Dialog offscreen nicht als modal (verifiziert) — der
    Dialog läuft trotzdem modal. Stattdessen wird er als Kind des
    bekannten Parents gesucht (deterministisch).
    """

    def click():
        boxes = parent.findChildren(MessageBox)
        assert boxes, "kein Dialog offen"
        getattr(boxes[-1], button_name).click()

    QTimer.singleShot(200, click)


def test_confirm_destructive_yes(qapp):
    parent = QWidget()
    parent.resize(800, 600)
    _click_dialog_button_later(parent, "yesButton")
    assert confirm_destructive(parent, "T", "Wirklich?") is True


def test_confirm_destructive_no(qapp):
    parent = QWidget()
    parent.resize(800, 600)
    _click_dialog_button_later(parent, "cancelButton")
    assert confirm_destructive(parent, "T", "Wirklich?") is False


def test_show_conflict_retry(qapp):
    parent = QWidget()
    parent.resize(800, 600)
    _click_dialog_button_later(parent, "yesButton")
    assert show_conflict(parent, "Belegt.") is True


def test_show_conflict_ok(qapp):
    parent = QWidget()
    parent.resize(800, 600)
    _click_dialog_button_later(parent, "cancelButton")
    assert show_conflict(parent, "Belegt.") is False


def test_show_conflict_without_retry(qapp):
    parent = QWidget()
    parent.resize(800, 600)
    _click_dialog_button_later(parent, "yesButton")
    assert show_conflict(parent, "Belegt.", allow_retry=False) is True
