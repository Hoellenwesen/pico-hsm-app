"""dkek_tab.py — DKEK-Bereich.

Sektionen: Status (Roh-Text, read-only), Key-Export (wrappen),
Key-Import (unwrappen). Core-Logik aus pico_hsm_tools/dkek_core.py
(sc-hsm-tool-Subprozesse).

BEWUSST NICHT ENTHALTEN (getroffene Entscheidung):
Create/Import-Share brauchen ein interaktives Terminal
(Custodian-Passworteingabe) und bleiben CLI-only — der Tab weist per
Hinweistext darauf hin, statt stdin-Raten ohne Hardware-Nachweis.
Share-Inhalte werden nirgends angezeigt oder zwischengespeichert.

Sicherheitsregeln (Konzept §8/§9, Muster wie PIN-Tab): PIN-Felder
strikt verdeckt (Auge-Button versteckt), nach Gebrauch geleert, nie
geloggt (dkek_core-Vertrag: keine PIN-Werte in Fehlertexten);
Schreibaktionen mit Confirm-Dialog (Unwrap mit Überschreiben-Hinweis).
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CompactSpinBox,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    TextEdit,
    TitleLabel,
)

from gui.session_helpers import close_info_bars, confirm_destructive
from gui.workers import FunctionWorker
from pico_hsm_tools import dkek_core as dc


def _ask_save_path(parent: QWidget, caption: str) -> str | None:
    """Speicher-Dialog (eigene Funktion: in Tests ohne echten Dialog)."""
    path, _ = QFileDialog.getSaveFileName(parent, caption)
    return path or None


def _ask_open_path(parent: QWidget, caption: str) -> str | None:
    """Öffnen-Dialog (eigene Funktion: in Tests ohne echten Dialog)."""
    path, _ = QFileDialog.getOpenFileName(parent, caption)
    return path or None


def _query_dkek_status() -> dict[str, Any]:
    """DKEK-Status lesen (läuft im Worker-Thread)."""
    try:
        text = dc.dkek_status()
        error: str | None = None
    except Exception as exc:  # noqa: BLE001 — als InfoBar, kein Abbruch
        text = None
        error = str(exc)
    return {"text": text, "error": error}


def _do_wrap(out_file: str, key_reference: int, pin: str) -> str:
    """Key wrappen/exportieren (läuft im Worker-Thread)."""
    dc.wrap_key(out_file, key_reference, pin)
    return out_file


def _do_unwrap(wrapped_file: str, key_reference: int, pin: str) -> int:
    """Key unwrappen/importieren (läuft im Worker-Thread)."""
    dc.unwrap_key(wrapped_file, key_reference, pin)
    return key_reference


class DkekTab(QWidget):
    """DKEK-Tab: Status, Wrap-Export, Unwrap-Import, Share-Hinweis."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("dkek")
        self._info_bars: list[InfoBar] = []
        self._worker: FunctionWorker | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(TitleLabel("DKEK", self))

        # --- Status ---------------------------------------------------
        status_head = QHBoxLayout()
        status_head.addWidget(StrongBodyLabel("DKEK-Status", self))
        status_head.addStretch(1)
        self.dkekRefreshButton = PushButton("Aktualisieren", self)
        self.dkekRefreshButton.setObjectName("dkekRefreshButton")
        self.dkekRefreshButton.clicked.connect(self.refresh)
        status_head.addWidget(self.dkekRefreshButton)
        layout.addLayout(status_head)
        self.dkekStatusText = TextEdit(self)
        self.dkekStatusText.setObjectName("dkekStatusText")
        self.dkekStatusText.setReadOnly(True)
        self.dkekStatusText.setPlaceholderText("Noch nicht abgefragt.")
        layout.addWidget(self.dkekStatusText)

        # --- Wrap (Export) ---------------------------------------------
        layout.addWidget(StrongBodyLabel("Key exportieren (wrappen)", self))
        wrap_file_row = QHBoxLayout()
        self.wrapFileEdit = LineEdit(self)
        self.wrapFileEdit.setObjectName("wrapFileEdit")
        self.wrapFileEdit.setPlaceholderText("Zieldatei für den gewrappten Key")
        self.wrapBrowseButton = PushButton("Durchsuchen", self)
        self.wrapBrowseButton.setObjectName("wrapBrowseButton")
        self.wrapBrowseButton.clicked.connect(self._on_wrap_browse)
        wrap_file_row.addWidget(self.wrapFileEdit, 1)
        wrap_file_row.addWidget(self.wrapBrowseButton)
        layout.addLayout(wrap_file_row)
        wrap_param_row = QHBoxLayout()
        self.wrapRefSpin = CompactSpinBox(self)
        self.wrapRefSpin.setObjectName("wrapRefSpin")
        self.wrapRefSpin.setRange(0, 65535)
        self.wrapRefSpin.setPrefix("Ref ")
        self.wrapButton = PrimaryPushButton("Exportieren", self)
        self.wrapButton.setObjectName("wrapButton")
        self.wrapButton.clicked.connect(self._on_wrap)
        wrap_param_row.addWidget(self.wrapRefSpin)
        wrap_param_row.addWidget(BodyLabel("PIN aus Anmeldung.", self), 1)
        wrap_param_row.addWidget(self.wrapButton)
        layout.addLayout(wrap_param_row)

        # --- Unwrap (Import) --------------------------------------------
        layout.addWidget(StrongBodyLabel("Key importieren (unwrappen)", self))
        unwrap_file_row = QHBoxLayout()
        self.unwrapFileEdit = LineEdit(self)
        self.unwrapFileEdit.setObjectName("unwrapFileEdit")
        self.unwrapFileEdit.setPlaceholderText("Gewrappte Key-Datei")
        self.unwrapBrowseButton = PushButton("Durchsuchen", self)
        self.unwrapBrowseButton.setObjectName("unwrapBrowseButton")
        self.unwrapBrowseButton.clicked.connect(self._on_unwrap_browse)
        unwrap_file_row.addWidget(self.unwrapFileEdit, 1)
        unwrap_file_row.addWidget(self.unwrapBrowseButton)
        layout.addLayout(unwrap_file_row)
        unwrap_param_row = QHBoxLayout()
        self.unwrapRefSpin = CompactSpinBox(self)
        self.unwrapRefSpin.setObjectName("unwrapRefSpin")
        self.unwrapRefSpin.setRange(0, 65535)
        self.unwrapRefSpin.setPrefix("Ref ")
        self.unwrapButton = PrimaryPushButton("Importieren", self)
        self.unwrapButton.setObjectName("unwrapButton")
        self.unwrapButton.clicked.connect(self._on_unwrap)
        unwrap_param_row.addWidget(self.unwrapRefSpin)
        unwrap_param_row.addWidget(BodyLabel("PIN aus Anmeldung.", self), 1)
        unwrap_param_row.addWidget(self.unwrapButton)
        layout.addLayout(unwrap_param_row)

        # --- Shares: CLI-only -------------------------------------------
        layout.addWidget(BodyLabel(
            "DKEK-Shares erzeugen/importieren (create-share, import-share) "
            "braucht ein interaktives Terminal für die Custodian-Eingabe "
            "und bleibt CLI-Sache: `pico-hsm-cli dkek create-share` bzw. "
            "`import-share` (optional mit --threshold/--total für n-of-m).",
            self,
        ))

        layout.addStretch(0)

    # --- Laden -----------------------------------------------------------

    def refresh(self) -> None:
        """DKEK-Status neu abfragen (Button + Tab-Wechsel)."""
        close_info_bars(self._info_bars)

        self.dkekRefreshButton.setEnabled(False)
        self._worker = FunctionWorker(_query_dkek_status)
        self._worker.signals.finished.connect(self._on_status_finished)
        self._worker.signals.error.connect(self._on_error)
        QThreadPool.globalInstance().start(self._worker)

    def _show_error(self, text: str) -> None:
        bar = InfoBar.error(
            "Fehler", text, parent=self,
            position=InfoBarPosition.TOP, duration=-1,
        )
        self._info_bars.append(bar)

    def _show_success(self, text: str) -> None:
        bar = InfoBar.success(
            "Erfolg", text, parent=self,
            position=InfoBarPosition.TOP, duration=5000,
        )
        self._info_bars.append(bar)

    def _on_status_finished(self, result: dict[str, Any]) -> None:
        self.dkekRefreshButton.setEnabled(True)
        self._worker = None
        if result["text"] is not None:
            self.dkekStatusText.setPlainText(result["text"])
        else:
            self.dkekStatusText.setPlainText("")
        if result["error"] is not None:
            self._show_error(result["error"])

    def _on_error(self, exc: Exception) -> None:
        """Sicherheitsnetz (eigentlich fängt _query alles ab)."""
        self.dkekRefreshButton.setEnabled(True)
        self.wrapButton.setEnabled(True)
        self.unwrapButton.setEnabled(True)
        self._worker = None
        self._show_error(f"Unerwarteter Fehler ({exc}).")

    # --- Dateien -----------------------------------------------------------

    def _on_wrap_browse(self) -> None:
        path = _ask_save_path(self, "Zieldatei für gewrappten Key wählen")
        if path:
            self.wrapFileEdit.setText(path)

    def _on_unwrap_browse(self) -> None:
        path = _ask_open_path(self, "Gewrappte Key-Datei wählen")
        if path:
            self.unwrapFileEdit.setText(path)

    # --- Schreiben -----------------------------------------------------------

    def _vault_pin(self) -> str | None:
        from gui.pin_vault import vault

        pin = vault.get()
        if not pin:
            self._show_error("Gesperrt — bitte zuerst anmelden (Start-Tab).")
            return None
        return pin

    def _on_wrap(self) -> None:
        out_file = self.wrapFileEdit.text().strip()
        if not out_file:
            self._show_error("Zieldatei angeben.")
            return
        pin = self._vault_pin()
        if not pin:
            return
        key_reference = self.wrapRefSpin.value()
        if not confirm_destructive(
            self,
            "Key exportieren",
            f"Key {key_reference} DKEK-verschlüsselt nach {out_file} "
            "exportieren?",
        ):
            return
        self.wrapButton.setEnabled(False)
        worker = FunctionWorker(_do_wrap, out_file, key_reference, pin)
        worker.signals.finished.connect(self._on_wrap_finished)
        worker.signals.error.connect(self._on_wrap_failed)
        self._worker = worker
        QThreadPool.globalInstance().start(worker)

    def _on_wrap_finished(self, out_file: str) -> None:
        self.wrapButton.setEnabled(True)
        self._worker = None
        self._show_success(f"Key exportiert nach {out_file}.")

    def _on_wrap_failed(self, exc: Exception) -> None:
        self.wrapButton.setEnabled(True)
        self._worker = None
        self._show_error(f"Export fehlgeschlagen ({exc}).")

    def _on_unwrap(self) -> None:
        wrapped_file = self.unwrapFileEdit.text().strip()
        if not wrapped_file:
            self._show_error("Quelldatei angeben.")
            return
        pin = self._vault_pin()
        if not pin:
            return
        key_reference = self.unwrapRefSpin.value()
        if not confirm_destructive(
            self,
            "Key importieren",
            f"Gewrappten Key als Reference {key_reference} importieren? "
            "ACHTUNG: ersetzt einen dort vorhandenen Key.",
        ):
            return
        self.unwrapButton.setEnabled(False)
        worker = FunctionWorker(_do_unwrap, wrapped_file, key_reference, pin)
        worker.signals.finished.connect(self._on_unwrap_finished)
        worker.signals.error.connect(self._on_unwrap_failed)
        self._worker = worker
        QThreadPool.globalInstance().start(worker)

    def _on_unwrap_finished(self, key_reference: int) -> None:
        self.unwrapButton.setEnabled(True)
        self._worker = None
        self._show_success(
            f"Key erfolgreich als Reference {key_reference} importiert."
        )

    def _on_unwrap_failed(self, exc: Exception) -> None:
        self.unwrapButton.setEnabled(True)
        self._worker = None
        self._show_error(f"Import fehlgeschlagen ({exc}).")
