"""pin_vault.py — app-weiter PIN-Cache (RAM only, nie Platte).

Einmal entsperren, Tabs nutzen die PIN ohne Neu-Eingabe (Vorbild
CliContext._pin_cache). Jede Nutzung setzt die Uhr zurück; 15 Minuten
ohne Nutzung oder explizites Sperren leeren den Cache. Kein Ersatz
für Sessions (die bleiben kurzlebig pro Worker-Call) — nur die PIN
muss nicht erneut eingetippt werden.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal

#: Timeout ohne PIN-Nutzung (ms).
UNLOCK_TIMEOUT_MS = 15 * 60 * 1000


class PinVault(QObject):
    """PIN-Cache mit Timeout. `lockedChanged` für UI-Anzeigen."""

    lockedChanged = Signal(bool)  # True = jetzt gesperrt

    def __init__(
        self,
        parent: QObject | None = None,
        timeout_ms: int = UNLOCK_TIMEOUT_MS,
    ) -> None:
        super().__init__(parent)
        self._timeout_ms = timeout_ms
        self._pin: str | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(timeout_ms)
        self._timer.timeout.connect(self.lock)

    @property
    def is_unlocked(self) -> bool:
        return self._pin is not None

    def unlock(self, pin: str) -> None:
        """PIN cachen + Uhr starten."""
        was_locked = self._pin is None
        self._pin = pin
        self._timer.start(self._timeout_ms)
        if was_locked:
            self.lockedChanged.emit(False)

    def get(self) -> str | None:
        """PIN holen (setzt die Timeout-Uhr zurück). None = gesperrt."""
        if self._pin is None:
            return None
        self._timer.start(self._timeout_ms)
        return self._pin

    def lock(self) -> None:
        """Cache sofort leeren (Sperren-Button, Timeout, App-Ende)."""
        if self._pin is None:
            return
        self._pin = None
        self._timer.stop()
        self.lockedChanged.emit(True)


#: App-weiter Vault (gui/app.py erzeugt/verwaltet ihn).
vault = PinVault()
