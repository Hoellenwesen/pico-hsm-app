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
    LogsTab,
    PinTab,
    SetupTab,
    StatusTab,
    WizardTab,
)
from pico_hsm_tools.device_mode import (
    MODE_HINT,
    MODE_LABEL,
    ROUTE_MODES,
    DeviceMode,
    DeviceState,
)

# (routeKey, Tab-Klasse, Icon, deutscher Nav-Text) — Wizard zuerst
# (Einstieg), dann CLI-Kommandogruppen, danach Logs (kein CLI-Pendant,
# Diagnose); Tests prüfen Vollständigkeit/Reihenfolge.
NAV_ITEMS: list[tuple[str, type[QWidget], FluentIcon, str]] = [
    ("wizard", WizardTab, FluentIcon.FLAG, "Start"),
    ("status", StatusTab, FluentIcon.INFO, "Status"),
    ("setup", SetupTab, FluentIcon.SETTING, "Setup"),
    ("pin", PinTab, FluentIcon.FINGERPRINT, "PIN"),
    ("dkek", DkekTab, FluentIcon.CERTIFICATE, "DKEK"),
    ("keys", KeysTab, FluentIcon.TAG, "Schlüssel"),
    ("backup", BackupTab, FluentIcon.SAVE, "Backup"),
    ("firmware", FirmwareTab, FluentIcon.UPDATE, "Firmware"),
    ("logs", LogsTab, FluentIcon.HISTORY, "Logs"),
]

THEME_TOGGLE_KEY = "theme"
THEME_TOGGLE_TEXT = "Design wechseln"

LOCK_TOGGLE_KEY = "lock"
LOCK_TOGGLE_TEXT_LOCKED = "Gesperrt — anmelden (Start-Tab)"
LOCK_TOGGLE_TEXT_UNLOCKED = "Entsperrt — sperren"


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
        self.navigationInterface.addItem(
            LOCK_TOGGLE_KEY,
            FluentIcon.PIN,
            LOCK_TOGGLE_TEXT_LOCKED,
            onClick=self._toggle_lock,
            selectable=False,
            position=NavigationItemPosition.BOTTOM,
        )
        self.navigationInterface.setCurrentItem(NAV_ITEMS[0][0])
        # Refresh-Muster (Duck-Typing): Tabs mit
        # refresh()-Methode aktualisieren sich bei jedem Wechsel auf sie.
        self.stackedWidget.currentChanged.connect(self._on_page_changed)

        # Modus-Gating: Status-Pill + Poller (strikt sperren, Tooltip
        # erklärt den benötigten Modus). Startet harmlos mit
        # KEIN_GERAET — modusfreie Tabs (Status, Backup) bleiben nutzbar.
        from gui.device_state import DeviceStatePoller, initial_state
        from gui.pin_vault import vault

        self._device_state = initial_state()
        self._poller = DeviceStatePoller(self)
        self._poller.modeChanged.connect(self._on_mode_changed)
        self._poller.start()
        self._apply_mode(self._device_state)
        self._lock_slot = lambda locked: self._update_lock_item(not locked)
        vault.lockedChanged.connect(self._lock_slot)
        self._update_lock_item(vault.is_unlocked)

    @property
    def device_state(self) -> DeviceState:
        return self._device_state

    def _mode_label(self) -> str:
        return f"[{MODE_LABEL[self._device_state.mode]}] {self._device_state.detail}"

    def _on_mode_changed(self, state: DeviceState) -> None:
        self._apply_mode(state)

    def _apply_mode(self, state: DeviceState) -> None:
        """Nav-Einträge je Modus sperren/freigeben + Sektions-Gating."""
        from pico_hsm_tools.device_mode import ROUTE_NEEDS_HINT

        self._device_state = state
        for route_key in self._tabs:
            allowed = ROUTE_MODES.get(route_key, {state.mode})
            nav_widget = self.navigationInterface.widget(route_key)
            enabled = state.mode in allowed
            if nav_widget is not None:
                nav_widget.setEnabled(enabled)
                hint = ROUTE_NEEDS_HINT.get(route_key, MODE_HINT[state.mode])
                nav_widget.setToolTip(
                    MODE_HINT[state.mode] if enabled else (
                        f"Deaktiviert im Modus {MODE_LABEL[state.mode]} — {hint}."
                    )
                )
            tab = self._tabs[route_key]
            gate = getattr(tab, "apply_device_mode", None)
            if callable(gate):
                gate(state.mode, state)

    def _on_page_changed(self, index: int) -> None:
        widget = self.stackedWidget.widget(index)
        refresh = getattr(widget, "refresh", None)
        if callable(refresh):
            refresh()

    def _toggle_theme(self, *_args: object) -> None:
        """Hell/Dunkel umschalten (QFluentWidgets toggleTheme)."""
        toggleTheme()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt-Konvention)
        """Vault-Signal trennen (globaler Vault überlebt das Fenster)."""
        from gui.pin_vault import vault

        try:
            vault.lockedChanged.disconnect(self._lock_slot)
        except (RuntimeError, TypeError):  # noqa: BLE001 — bereits getrennt
            pass
        super().closeEvent(event)

    def _toggle_lock(self, *_args: object) -> None:
        """Vault sperren (PIN-Cache leeren). Entsperren nur per Anmeldung."""
        from gui.pin_vault import vault

        vault.lock()
        self._update_lock_item(vault.is_unlocked)

    def _update_lock_item(self, unlocked: bool) -> None:
        nav_widget = self.navigationInterface.widget(LOCK_TOGGLE_KEY)
        if nav_widget is not None:
            try:
                nav_widget.setText(
                    LOCK_TOGGLE_TEXT_UNLOCKED if unlocked
                    else LOCK_TOGGLE_TEXT_LOCKED
                )
            except Exception:  # noqa: BLE001 — Text-API je Version
                pass
            nav_widget.setToolTip(
                "PIN-Cache leeren (jetzt entsperrt)."
                if unlocked else "Gesperrt — Anmeldung im Start-Tab."
            )

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
