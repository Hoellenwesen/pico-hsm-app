"""firmware_tab.py — Firmware-Bereich (Preflight + geführtes Flashen).

Preflight-Prüfung, geführter Flash-Ablauf (Preflight -> TOTP -> BOOTSEL ->
Flash mit Log-Ansicht). Core-Logik aus pico_hsm_tools/flash_core.py.
Das Audit-Log wohnt im Logs-Tab (eine Wahrheit, keine Duplikate) —
der Chain-Guard vor Preflight/Flash bleibt (Schutzlogik, keine Anzeige).

Getroffene Entscheidungen: geführter einstufiger Ablauf über einen
„Flashen"-Button, Fortschritt als Log-Ansicht (Core liefert
on_progress-Texte — per Signal in den UI-Thread gemarshallt), TOTP nur
per Eingabe-Dialog wenn die Secret-Datei existiert (Code offen wie CLI).

Sicherheitsregeln: gebrochene Audit-Chain verlangt Bestätigung
(CLI-Parität „Trotzdem fortfahren?"), TOTP-Fehlschlag protokolliert
`rejected_totp` und bricht ab (CLI-Parität), BOOTSEL-Bestätigung vor
dem Flash, Buttons während Läufen gesperrt.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QThreadPool, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    TextEdit,
    TitleLabel,
)

from gui.session_helpers import confirm_destructive
from gui.workers import FunctionWorker
from pico_hsm_tools import flash_core as fc
from pico_hsm_tools import audit_log


def _ask_open_file(parent: QWidget, caption: str) -> str | None:
    """Öffnen-Dialog (eigene Funktion: in Tests ohne echten Dialog)."""
    path, _ = QFileDialog.getOpenFileName(parent, caption)
    return path or None


def _ask_totp_code(parent: QWidget) -> str | None:
    """TOTP-Code-Dialog (nur wenn Secret-Datei existiert). Code offen
    wie im CLI (kein Geheimnis — Einmal-Code vom getrennten Gerät).
    Gibt None bei Abbruch zurück."""
    text, ok = QInputDialog.getText(
        parent, "TOTP-Autorisierung",
        "TOTP-Code vom getrennten Gerät:",
        QLineEdit.Normal, "",
    )
    return text.strip() if ok else None


class _ProgressSignaler(QObject):
    """Trägt on_progress-Texte aus dem Worker-Thread in den UI-Thread
    (Qt queued connection — direkter Widget-Zugriff wäre illegal)."""

    progress = Signal(str)


class FirmwareTab(QWidget):
    """Firmware-Tab: Preflight, geführtes Flashen mit Log, Audit."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("firmware")
        self._info_bars: list[InfoBar] = []
        self._worker: FunctionWorker | None = None
        self._progress_signaler = _ProgressSignaler()
        self._progress_signaler.progress.connect(self._append_log)

        layout = QVBoxLayout(self)
        layout.addWidget(TitleLabel("Firmware", self))

        # --- Datei -------------------------------------------------------
        file_row = QHBoxLayout()
        self.fwFileEdit = LineEdit(self)
        self.fwFileEdit.setObjectName("fwFileEdit")
        self.fwFileEdit.setPlaceholderText("Firmware-Datei (.uf2)")
        self.fwBrowseButton = PushButton("Durchsuchen", self)
        self.fwBrowseButton.setObjectName("fwBrowseButton")
        self.fwBrowseButton.clicked.connect(self._on_fw_browse)
        file_row.addWidget(self.fwFileEdit, 1)
        file_row.addWidget(self.fwBrowseButton)
        layout.addLayout(file_row)

        # --- Preflight ----------------------------------------------------
        preflight_head = QHBoxLayout()
        preflight_head.addWidget(StrongBodyLabel("Vorab-Prüfung", self))
        preflight_head.addStretch(1)
        self.preflightButton = PrimaryPushButton("Prüfen", self)
        self.preflightButton.setObjectName("preflightButton")
        self.preflightButton.clicked.connect(self._on_preflight)
        preflight_head.addWidget(self.preflightButton)
        layout.addLayout(preflight_head)
        self.preflightResultLabel = BodyLabel("Noch nicht geprüft.", self)
        self.preflightResultLabel.setObjectName("preflightResultLabel")
        self.preflightResultLabel.setWordWrap(True)
        layout.addWidget(self.preflightResultLabel)

        # --- Flash ----------------------------------------------------------
        flash_head = QHBoxLayout()
        flash_head.addWidget(StrongBodyLabel("Flashen", self))
        flash_head.addStretch(1)
        self.flashButton = PrimaryPushButton("Flashen", self)
        self.flashButton.setObjectName("flashButton")
        self.flashButton.clicked.connect(self._on_flash)
        flash_head.addWidget(self.flashButton)
        layout.addLayout(flash_head)
        self.flashLog = TextEdit(self)
        self.flashLog.setObjectName("flashLog")
        self.flashLog.setReadOnly(True)
        self.flashLog.setPlaceholderText("Flash-Protokoll")
        self.flashLog.setMaximumHeight(120)
        layout.addWidget(self.flashLog)

        layout.addStretch(0)

    def apply_device_mode(self, mode: object, _state: object = None) -> None:
        """Nur Flash-Button braucht BOOTSEL; Preflight/Audit bleiben nutzbar."""
        from pico_hsm_tools.device_mode import DeviceMode

        bootsel = mode == DeviceMode.BOOTSEL
        self.flashButton.setEnabled(bootsel)
        self.flashButton.setToolTip(
            "" if bootsel else "Flashen braucht BOOTSEL-Modus."
        )

    # --- Helfer ------------------------------------------------------------

    def _show_error(self, text: str) -> None:
        bar = InfoBar.error(
            "Fehler", text, parent=self,
            position=InfoBarPosition.TOP, duration=-1,
        )
        self._info_bars.append(bar)

    def _show_success(self, text: str) -> None:
        bar = InfoBar.success(
            "Erfolg", text, parent=self,
            position=InfoBarPosition.TOP, duration=8000,
        )
        self._info_bars.append(bar)

    def _show_warning(self, text: str) -> None:
        bar = InfoBar.warning(
            "Hinweis", text, parent=self,
            position=InfoBarPosition.TOP, duration=-1,
        )
        self._info_bars.append(bar)

    def _start_worker(
        self,
        make_worker: Callable[[], FunctionWorker],
        on_finished: Callable[[Any], None],
        *buttons: PushButton,
    ) -> None:
        """Worker starten (Buttons sperren, Fehler als InfoBar)."""
        for button in buttons:
            button.setEnabled(False)
        worker = make_worker()

        def _finished(result: Any) -> None:
            for button in buttons:
                button.setEnabled(True)
            self._worker = None
            on_finished(result)

        def _failed(exc: Exception) -> None:
            for button in buttons:
                button.setEnabled(True)
            self._worker = None
            self._show_error(f"Fehlgeschlagen ({exc}).")

        worker.signals.finished.connect(_finished)
        worker.signals.error.connect(_failed)
        self._worker = worker
        QThreadPool.globalInstance().start(worker)

    def _check_chain_or_abort(self) -> bool:
        """Audit-Chain prüfen; bei Bruch Confirm, sonst True."""
        if audit_log.fc.verify_audit_chain():
            return True
        return confirm_destructive(
            self,
            "Audit-Log prüfen",
            "Audit-Log-Hash-Chain ist gebrochen — Log möglicherweise "
            "verändert. Trotzdem fortfahren?",
        )

    def _fw_path_or_error(self) -> Path | None:
        text = self.fwFileEdit.text().strip()
        if not text:
            self._show_error("Firmware-Datei (.uf2) auswählen.")
            return None
        return Path(text)

    def _append_log(self, message: str) -> None:
        self.flashLog.append(message)

    # --- Dateien -------------------------------------------------------------

    def _on_fw_browse(self) -> None:
        path = _ask_open_file(self, "Firmware-Datei (.uf2) wählen")
        if path:
            self.fwFileEdit.setText(path)

    # --- Preflight -------------------------------------------------------------

    def _on_preflight(self) -> None:
        fw_path = self._fw_path_or_error()
        if fw_path is None:
            return
        if not self._check_chain_or_abort():
            return

        def make() -> FunctionWorker:
            return FunctionWorker(fc.run_preflight, fw_path)

        def done(result: fc.PreflightResult) -> None:
            version = result.version
            verdict = (
                "[OK] Signatur gültig" if result.signature_checked
                else "[OK] Prüfungen bestanden (unsignierte Firmware)"
            )
            fingerprint_note = (
                "Board-Fingerprint stimmt" if result.fingerprint_checked
                else "[WARN] Secure Boot aus — Fingerprint-Check übersprungen"
            )
            self.preflightResultLabel.setText(
                f"{verdict} · {fingerprint_note} · "
                f"Version {fc.format_version_rollback(version)} · "
                f"SHA-256: {result.sha256}"
            )

        self._start_worker(make, done, self.preflightButton, self.flashButton)

    # --- Flash (geführt) ----------------------------------------------------------

    def _on_flash(self) -> None:
        fw_path = self._fw_path_or_error()
        if fw_path is None:
            return
        if not self._check_chain_or_abort():
            return

        def make() -> FunctionWorker:
            return FunctionWorker(fc.run_preflight, fw_path)

        self._start_worker(
            make, self._on_preflight_for_flash,
            self.preflightButton, self.flashButton,
        )

    def _on_preflight_for_flash(self, result: fc.PreflightResult) -> None:
        version = result.version
        self.preflightResultLabel.setText(
            f"[OK] Vorab-Prüfungen bestanden: Version "
            f"{fc.format_version_rollback(version)}"
        )
        if not result.fingerprint_checked:
            self._show_warning(
                "Secure Boot aus — Fingerprint-Check übersprungen."
            )
        if not result.signature_checked:
            self._show_warning(
                "Unsignierte Firmware — Signatur-Check übersprungen."
            )

        if fc.TOTP_SECRET_FILE.exists():
            secret = fc.TOTP_SECRET_FILE.read_text().strip()
            code = _ask_totp_code(self)
            if code is None:
                return
            try:
                valid = fc.verify_totp_code(secret, code)
            except Exception as exc:  # noqa: BLE001
                self._show_error(f"TOTP-Prüfung fehlgeschlagen ({exc}).")
                return
            if not valid:
                fc.append_audit({
                    "file": str(result.fw_path), "sha256": result.sha256,
                    "status": "rejected_totp",
                })
                self._show_error("TOTP-Code ungültig oder abgelaufen.")
                return
            self._show_success("TOTP-Autorisierung bestätigt.")

        if not confirm_destructive(
            self,
            "Flashen",
            "Board jetzt in BOOTSEL-Modus versetzen und mit Flashen "
            "fortfahren?",
        ):
            return

        self.flashLog.setPlainText("")

        def make() -> FunctionWorker:
            return FunctionWorker(
                fc.do_flash, result,
                on_progress=self._progress_signaler.progress.emit,
            )

        def done(_result: None) -> None:
            self._show_success("Firmware erfolgreich geflasht.")

        self._start_worker(
            make, done, self.preflightButton, self.flashButton,
        )

    # --- Hinweis -----------------------------------------------------------
    # Das Audit-Log wohnt im Logs-Tab; dieser Tab hat keinen eigenen
    # Refresh (Tab-Wechsel löst nichts aus — Preflight/Flash nur per Button).
