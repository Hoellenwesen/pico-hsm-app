"""
workers.py — QThread/QRunnable-Wrapper für lange Operationen (Konzept §8).

Regel: Jeder Aufruf, der einen Subprozess startet oder eine potenziell
lange PKCS#11-Operation ist (v.a. `keys generate` bei RSA-2048/4096),
läuft in einem QRunnable über QThreadPool — Ergebnis per Qt-Signal
zurück in den Main-Thread. Blockierender Direktaufruf im UI-Thread ist
für keinen Fall zulässig, der länger als ~1s dauern kann.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, Signal


class _WorkerSignals(QObject):
    """Signal-Träger (QRunnable selbst kann keine Signals besitzen)."""

    finished = Signal(object)  # Ergebnis der Funktion
    error = Signal(Exception)  # aufgetretene Ausnahme


class FunctionWorker(QRunnable):
    """Führt `fn(*args, **kwargs)` im Thread-Pool aus.

    Erfolg -> `signals.finished.emit(ergebnis)`,
    Ausnahme -> `signals.error.emit(ausnahme)` — beides im Main-Thread
    beim Empfänger (Qt queued connection über Thread-Grenze).
    """

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self.signals = _WorkerSignals()

    def run(self) -> None:
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as exc:  # noqa: BLE001 — wird als Signal gemeldet
            self.signals.error.emit(exc)
        else:
            self.signals.finished.emit(result)
