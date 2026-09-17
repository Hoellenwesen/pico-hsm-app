"""
main_window.py — Hauptfenster (FluentWindow, Sidebar-Navigation).

1 Bereich pro CLI-Kommandogruppe (Konzept §8): Status, Setup, PIN,
DKEK, Schlüssel, Backup, Firmware. Theme-Umschalter als unterer
Navigations-Eintrag (kein separates Menü nötig).
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget
from qfluentwidgets import (
    FluentIcon,
    FluentWindow,
    NavigationItemPosition,
    isDarkTheme,
    toggleTheme,
)

from gui.tabs import (
    BackupTab,
    DkekTab,
    FirmwareTab,
    KeysTab,
    PinTab,
    SetupTab,
    StatusTab,
)

# (routeKey, Tab-Klasse, Icon, deutscher Nav-Text) — Reihenfolge wie die
# CLI-Kommandogruppen; Tests prüfen Vollständigkeit/Reihenfolge.
NAV_ITEMS: list[tuple[str, type[QWidget], FluentIcon, str]] = [
    ("status", StatusTab, FluentIcon.INFO, "Status"),
    ("setup", SetupTab, FluentIcon.SETTING, "Setup"),
    ("pin", PinTab, FluentIcon.FINGERPRINT, "PIN"),
    ("dkek", DkekTab, FluentIcon.CERTIFICATE, "DKEK"),
    ("keys", KeysTab, FluentIcon.TAG, "Schlüssel"),
    ("backup", BackupTab, FluentIcon.SAVE, "Backup"),
    ("firmware", FirmwareTab, FluentIcon.UPDATE, "Firmware"),
]

THEME_TOGGLE_KEY = "theme"
THEME_TOGGLE_TEXT = "Design wechseln"


class MainWindow(FluentWindow):
    """Hauptfenster der Pico-HSM-App (Dark-Default, siehe gui/app.py)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Pico HSM")
        self.resize(1000, 700)

        self._tabs: dict[str, QWidget] = {}
        for route_key, tab_cls, icon, text in NAV_ITEMS:
            widget = tab_cls(self)
            self._tabs[route_key] = widget
            self.addSubInterface(widget, icon, text)

        self.navigationInterface.addItem(
            THEME_TOGGLE_KEY,
            FluentIcon.BRIGHTNESS,
            THEME_TOGGLE_TEXT,
            onClick=self._toggle_theme,
            selectable=False,
            position=NavigationItemPosition.BOTTOM,
        )
        self.navigationInterface.setCurrentItem(NAV_ITEMS[0][0])
        # Refresh-Muster für Schritt-6-Tabs (Duck-Typing): Tabs mit
        # refresh()-Methode aktualisieren sich bei jedem Wechsel auf sie.
        self.stackedWidget.currentChanged.connect(self._on_page_changed)

    def _on_page_changed(self, index: int) -> None:
        widget = self.stackedWidget.widget(index)
        refresh = getattr(widget, "refresh", None)
        if callable(refresh):
            refresh()

    def _toggle_theme(self, *_args: object) -> None:
        """Hell/Dunkel umschalten (QFluentWidgets toggleTheme)."""
        toggleTheme()

    @property
    def is_dark(self) -> bool:
        """Aktuell Dunkel-Modus aktiv? (Für Tests/Anzeige.)"""
        return isDarkTheme()

    def tab(self, route_key: str) -> QWidget:
        """Tab-Widget per Routen-Key (KeyError bei unbekanntem Key)."""
        return self._tabs[route_key]

    def show_tab(self, route_key: str) -> None:
        """Programmatisch auf einen Tab wechseln (z.B. „Zum DKEK-Tab"-
        Button) — derselbe Signal-Pfad wie ein Nav-Klick (kein
        Interna-Griff vom Tab aus)."""
        nav_widget = self.navigationInterface.widget(route_key)
        assert nav_widget is not None, f"unbekannte Route: {route_key}"
        nav_widget.clicked.emit(True)
