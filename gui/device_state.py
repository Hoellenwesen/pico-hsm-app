"""device_state.py — periodischer Modus-Poll für die GUI (3s, Worker-Thread).

Fragt `pico_hsm_tools.device_mode.detect()` im QThreadPool ab, meldet
Änderungen per `modeChanged`-Signal in den Main-Thread. Tabs sperren
nie selbst — das macht MainWindow zentral.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QThreadPool, QTimer, Signal

from gui.workers import FunctionWorker
from pico_hsm_tools.device_mode import DeviceMode, DeviceState, detect

POLL_INTERVAL_MS = 3000


class DeviceStatePoller(QObject):
    """Pollt den Geräte-Modus, emittiert nur bei Änderung."""

    modeChanged = Signal(object)  # DeviceState

    def __init__(
        self,
        parent: QObject | None = None,
        interval_ms: int = POLL_INTERVAL_MS,
    ) -> None:
        super().__init__(parent)
        self._interval_ms = interval_ms
        self._current: DeviceState | None = None
        self._busy = False
        self._worker: FunctionWorker | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self.poll_once)

    def start(self) -> None:
        self.poll_once()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def poll_once(self) -> None:
        if self._busy:
            return
        self._busy = True
        worker = FunctionWorker(detect)
        self._worker = worker
        worker.signals.finished.connect(self._on_finished)
        worker.signals.error.connect(self._on_error)
        QThreadPool.globalInstance().start(worker)

    def _on_finished(self, state: object) -> None:
        self._busy = False
        self._worker = None
        assert isinstance(state, DeviceState)
        if self._current is None or state.mode != self._current.mode:
            self._current = state
            self.modeChanged.emit(state)
        else:
            self._current = state

    def _on_error(self, _exc: Exception) -> None:
        self._busy = False
        self._worker = None


def initial_state() -> DeviceState:
    """Startzustand vor erstem Poll: kein Gerät (Tabs modusfrei bleiben)."""
    return DeviceState(
        mode=DeviceMode.KEIN_GERAET,
        picotool_ok=True,
        token_present=False,
        detail="Initial — warte auf ersten Poll",
    )
