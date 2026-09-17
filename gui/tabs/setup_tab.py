"""setup_tab.py — Setup-Bereich (Schritt 6b: zweiter ausgebauter Tab).

Drei Sektionen wie CLI `setup show` / `setup datetime` /
`setup dynamic-options`: OTP-Anzeige (read-only, picotool über
flash_core.py), RTC-Datetime (apdu_core.py), Dynamic Options
(apdu_core.py). Ein FunctionWorker pro Vorgang; Fehler pro Sektion
blockieren einander nicht und erscheinen als inline InfoBar.

Schreibaktionen (Datetime setzen, Options anwenden) verlangen einen
Confirm-Dialog; das Deaktivieren von Press-to-Confirm bekommt einen
verschärften Warntext (Konzept §9). APDU-Verbindungen werden im Worker
geöffnet/geschlossen (nie UI-Thread, sequenziell/exklusiv-Regel §7.b).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6.QtCore import QDateTime, QThreadPool
from PySide6.QtWidgets import QHBoxLayout, QTableWidgetItem, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    DateTimeEdit,
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    SwitchButton,
    TableWidget,
    TitleLabel,
)

from gui.session_helpers import confirm_destructive
from gui.workers import FunctionWorker
from pico_hsm_tools import apdu_core as ac
from pico_hsm_tools import flash_core as fc


def _query_setup() -> dict[str, Any]:
    """OTP + Datetime + Options einsammeln (läuft im Worker-Thread)."""
    errors: list[str] = []

    try:
        fingerprint = fc.get_burned_key_fingerprint()
        fingerprint_error: str | None = None
    except Exception as exc:  # noqa: BLE001 — als InfoBar, kein Abbruch
        fingerprint = None
        fingerprint_error = f"Fingerprint nicht lesbar ({exc})."
        errors.append(fingerprint_error)
    otp_flags = [(name, fc.read_otp_field(field)) for field, name in fc.OTP_FLAG_FIELDS]

    try:
        conn = ac.open_connection()
        try:
            current_dt = ac.get_datetime(conn)
            current_options = ac.get_dynamic_options(conn)
        finally:
            conn.disconnect()
        apdu_error: str | None = None
    except Exception as exc:  # noqa: BLE001 — als InfoBar, kein Abbruch
        current_dt = None
        current_options = None
        apdu_error = f"Token nicht erreichbar ({exc})."
        errors.append(apdu_error)

    return {
        "fingerprint": fingerprint,
        "otp_flags": otp_flags,
        "datetime": current_dt.isoformat(timespec="seconds") if current_dt else None,
        "press_to_confirm": current_options.press_to_confirm if current_options else None,
        "key_usage_counter": current_options.key_usage_counter if current_options else None,
        "errors": errors,
    }


def _do_set_datetime(iso_value: str) -> str:
    """RTC-Datetime setzen (läuft im Worker-Thread). Gibt ISO zurück."""
    value = datetime.fromisoformat(iso_value)
    conn = ac.open_connection()
    try:
        ac.set_datetime(conn, value)
    finally:
        conn.disconnect()
    return value.isoformat(timespec="seconds")


def _do_set_options(press_to_confirm: bool, key_usage_counter: bool) -> dict[str, bool]:
    """Dynamic Options setzen (läuft im Worker-Thread)."""
    options = ac.DynamicOptions(
        press_to_confirm=press_to_confirm,
        key_usage_counter=key_usage_counter,
    )
    conn = ac.open_connection()
    try:
        ac.set_dynamic_options(conn, options)
    finally:
        conn.disconnect()
    return {"press_to_confirm": press_to_confirm, "key_usage_counter": key_usage_counter}


class SetupTab(QWidget):
    """Setup-Tab: OTP-Anzeige, RTC-Datetime, Dynamic Options."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("setup")
        self._info_bars: list[InfoBar] = []
        self._worker: FunctionWorker | None = None
        self._current_ptc: bool | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(TitleLabel("Setup", self))

        # --- OTP ------------------------------------------------------
        otp_head = QHBoxLayout()
        otp_head.addWidget(StrongBodyLabel("Secure Boot / OTP", self))
        otp_head.addStretch(1)
        self.setupRefreshButton = PrimaryPushButton("Aktualisieren", self)
        self.setupRefreshButton.setObjectName("setupRefreshButton")
        self.setupRefreshButton.clicked.connect(self.refresh)
        otp_head.addWidget(self.setupRefreshButton)
        layout.addLayout(otp_head)
        self.fingerprintLabel = BodyLabel("Noch nicht abgefragt.", self)
        self.fingerprintLabel.setObjectName("fingerprintLabel")
        layout.addWidget(self.fingerprintLabel)
        self.otpTable = TableWidget(self)
        self.otpTable.setObjectName("otpTable")
        self.otpTable.setColumnCount(2)
        self.otpTable.setHorizontalHeaderLabels(["Feld", "Wert"])
        self.otpTable.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.otpTable)

        # --- Datetime -------------------------------------------------
        layout.addWidget(StrongBodyLabel("RTC-Datetime", self))
        self.datetimeCurrentLabel = BodyLabel("Noch nicht abgefragt.", self)
        self.datetimeCurrentLabel.setObjectName("datetimeCurrentLabel")
        layout.addWidget(self.datetimeCurrentLabel)
        datetime_row = QHBoxLayout()
        self.datetimeEdit = DateTimeEdit(self)
        self.datetimeEdit.setObjectName("datetimeEdit")
        self.datetimeEdit.setDateTime(QDateTime.currentDateTime())
        self.datetimeEdit.setCalendarPopup(True)
        self.nowButton = PushButton("Jetzt", self)
        self.nowButton.setObjectName("nowButton")
        self.nowButton.clicked.connect(self._fill_now)
        self.datetimeSetButton = PrimaryPushButton("Setzen", self)
        self.datetimeSetButton.setObjectName("datetimeSetButton")
        self.datetimeSetButton.clicked.connect(self._on_set_datetime)
        datetime_row.addWidget(self.datetimeEdit, 1)
        datetime_row.addWidget(self.nowButton)
        datetime_row.addWidget(self.datetimeSetButton)
        layout.addLayout(datetime_row)

        # --- Dynamic Options ------------------------------------------
        layout.addWidget(StrongBodyLabel("Dynamic Options", self))
        options_row = QHBoxLayout()
        self.ptcSwitch = SwitchButton("Press-to-Confirm", self)
        self.ptcSwitch.setObjectName("ptcSwitch")
        self.counterSwitch = SwitchButton("Key-Usage-Counter", self)
        self.counterSwitch.setObjectName("counterSwitch")
        self.dynoptsApplyButton = PrimaryPushButton("Anwenden", self)
        self.dynoptsApplyButton.setObjectName("dynoptsApplyButton")
        self.dynoptsApplyButton.clicked.connect(self._on_apply_options)
        options_row.addWidget(self.ptcSwitch)
        options_row.addWidget(self.counterSwitch)
        options_row.addStretch(1)
        options_row.addWidget(self.dynoptsApplyButton)
        layout.addLayout(options_row)

        layout.addStretch(0)

    # --- Laden -----------------------------------------------------------

    def refresh(self) -> None:
        """Alle Sektionen neu abfragen (Button + Tab-Wechsel)."""
        for bar in self._info_bars:
            bar.close()
        self._info_bars.clear()

        self.setupRefreshButton.setEnabled(False)
        self._worker = FunctionWorker(_query_setup)
        self._worker.signals.finished.connect(self._on_finished)
        self._worker.signals.error.connect(self._on_error)
        QThreadPool.globalInstance().start(self._worker)

    def _show_error(self, text: str) -> None:
        bar = InfoBar.error(
            "Fehler", text, parent=self,
            position=InfoBarPosition.TOP, duration=-1,
        )
        self._info_bars.append(bar)

    def _on_finished(self, result: dict[str, Any]) -> None:
        self.setupRefreshButton.setEnabled(True)
        self._worker = None

        fingerprint = result["fingerprint"]
        self.fingerprintLabel.setText(
            f"Pubkey-Fingerprint: {fingerprint}"
            if fingerprint is not None else "Fingerprint nicht lesbar."
        )
        flags = result["otp_flags"]
        self.otpTable.setRowCount(len(flags))
        for row, (name, value) in enumerate(flags):
            self.otpTable.setItem(row, 0, QTableWidgetItem(name))
            self.otpTable.setItem(row, 1, QTableWidgetItem(value))

        current_dt = result["datetime"]
        self.datetimeCurrentLabel.setText(
            f"Aktuell: {current_dt.replace('T', ' ')}"
            if current_dt is not None else "Aktuell: nicht lesbar."
        )

        self._current_ptc = result["press_to_confirm"]
        if self._current_ptc is not None:
            self.ptcSwitch.setChecked(self._current_ptc)
        counter = result["key_usage_counter"]
        if counter is not None:
            self.counterSwitch.setChecked(counter)

        for message in result["errors"]:
            self._show_error(message)

    def _on_error(self, exc: Exception) -> None:
        """Sicherheitsnetz (eigentlich fängt _query_setup alles ab)."""
        self.setupRefreshButton.setEnabled(True)
        self._worker = None
        self._show_error(f"Unerwarteter Fehler ({exc}).")

    # --- Schreiben ---------------------------------------------------------

    def _fill_now(self) -> None:
        self.datetimeEdit.setDateTime(QDateTime.currentDateTime())

    def _on_set_datetime(self) -> None:
        value = self.datetimeEdit.dateTime().toPython()
        if not confirm_destructive(
            self,
            "RTC-Datetime setzen",
            f"Token-Uhr auf {value.strftime('%Y-%m-%d %H:%M:%S')} setzen?",
        ):
            return
        self.datetimeSetButton.setEnabled(False)
        worker = FunctionWorker(_do_set_datetime, value.isoformat(timespec="seconds"))
        worker.signals.finished.connect(self._on_set_finished)
        worker.signals.error.connect(self._on_set_failed)
        self._worker = worker
        QThreadPool.globalInstance().start(worker)

    def _on_set_finished(self, iso_value: str) -> None:
        self.datetimeSetButton.setEnabled(True)
        self._worker = None
        self.datetimeCurrentLabel.setText(f"Aktuell: {iso_value.replace('T', ' ')}")

    def _on_set_failed(self, exc: Exception) -> None:
        self.datetimeSetButton.setEnabled(True)
        self._worker = None
        self._show_error(f"Datetime setzen fehlgeschlagen ({exc}).")

    def _on_apply_options(self) -> None:
        target_ptc = self.ptcSwitch.isChecked()
        target_counter = self.counterSwitch.isChecked()
        if not target_ptc and (self._current_ptc is None or self._current_ptc):
            title = "Press-to-Confirm DEAKTIVIEREN"
            text = (
                "Danach bestätigt das Token private/geheime Key-Operationen "
                "OHNE Tastendruck — Schutz vor versteckten Operationen "
                "entfällt. Wirklich deaktivieren?"
            )
        else:
            title = "Dynamic Options anwenden"
            text = (
                f"Press-to-Confirm: {'an' if target_ptc else 'aus'}, "
                f"Key-Usage-Counter: {'an' if target_counter else 'aus'} — "
                "übernehmen?"
            )
        if not confirm_destructive(self, title, text):
            return
        self.dynoptsApplyButton.setEnabled(False)
        worker = FunctionWorker(_do_set_options, target_ptc, target_counter)
        worker.signals.finished.connect(self._on_apply_finished)
        worker.signals.error.connect(self._on_apply_failed)
        self._worker = worker
        QThreadPool.globalInstance().start(worker)

    def _on_apply_finished(self, result: dict[str, bool]) -> None:
        self.dynoptsApplyButton.setEnabled(True)
        self._worker = None
        self._current_ptc = result["press_to_confirm"]
        self.ptcSwitch.setChecked(result["press_to_confirm"])
        self.counterSwitch.setChecked(result["key_usage_counter"])

    def _on_apply_failed(self, exc: Exception) -> None:
        self.dynoptsApplyButton.setEnabled(True)
        self._worker = None
        self._show_error(f"Options setzen fehlgeschlagen ({exc}).")
