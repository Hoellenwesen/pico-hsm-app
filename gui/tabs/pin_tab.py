"""pin_tab.py — PIN-Bereich.

Drei Sektionen wie CLI `pin change`/`unblock`/`status`: Ändern,
Entsperren, Status. Core-Logik aus pico_hsm_tools/pin_core.py
(pkcs11-tool-Subprozesse — python-pkcs11 hat keine PIN-API).

Getroffene Entscheidungen: alle drei Sektionen, neue PIN
zweimal (Abgleich wie CLI), strikt verdeckte Felder (QFluentWidgets
PasswordLineEdit bringt einen eingebauten Auge-Button mit — wird hier
bewusst versteckt, siehe _pin_field), Warn-InfoBar bei kritischem
PIN-Status.

Sicherheitsregeln (Konzept §8/§9): kein Logging, kein Klartext in
Exceptions (pin_core-Vertrag: keine PIN-Werte in Fehlertexten),
PIN-Felder nach erfolgreicher Aktion leeren, kein Copy-Paste
(PasswordLineEdit setzt NoContextMenu), kein Autofill.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    InfoBar,
    InfoBarPosition,
    PasswordLineEdit,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    TitleLabel,
)

from gui.session_helpers import close_info_bars, selected_serial
from gui.workers import FunctionWorker
from pico_hsm_tools import pin_core as pc


def _pin_field(parent: QWidget, name: str) -> PasswordLineEdit:
    """Strikt verdecktes PIN-Feld (Auge-Button versteckt — Entscheidung)."""
    field = PasswordLineEdit(parent)
    field.setObjectName(name)
    field.viewButton.hide()
    return field


def _query_pin_flags() -> dict[str, Any]:
    """PIN-Status lesen (läuft im Worker-Thread)."""
    try:
        flags = pc.read_pin_flags(serial=selected_serial())
        error: str | None = None
    except Exception as exc:  # noqa: BLE001 — als InfoBar, kein Abbruch
        flags = None  # type: ignore[assignment]
        error = str(exc)
    return {"flags": flags, "error": error}


def _do_change(old_pin: str, new_pin: str) -> None:
    """User-PIN ändern (läuft im Worker-Thread)."""
    pc.change_user_pin(old_pin, new_pin, serial=selected_serial())


def _do_unblock(so_pin: str, new_pin: str) -> None:
    """User-PIN entsperren (läuft im Worker-Thread)."""
    pc.unblock_user_pin(so_pin, new_pin, serial=selected_serial())


class PinTab(QWidget):
    """PIN-Tab: Ändern, Entsperren, Status."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("pin")
        self._info_bars: list[InfoBar] = []
        self._worker: FunctionWorker | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(TitleLabel("PIN", self))

        # --- Ändern ---------------------------------------------------
        layout.addWidget(StrongBodyLabel("User-PIN ändern", self))
        layout.addWidget(BodyLabel(
            "Alte PIN kommt aus der Anmeldung (Vault) — nur die neue "
            "PIN hier zweimal eingeben.", self,
        ))
        self.newPinEdit1 = _pin_field(self, "newPinEdit1")
        self.newPinEdit1.setPlaceholderText("Neue User-PIN")
        self.newPinEdit2 = _pin_field(self, "newPinEdit2")
        self.newPinEdit2.setPlaceholderText("Neue User-PIN bestätigen")
        layout.addWidget(self.newPinEdit1)
        layout.addWidget(self.newPinEdit2)
        self.changeButton = PrimaryPushButton("Ändern", self)
        self.changeButton.setObjectName("changeButton")
        self.changeButton.clicked.connect(self._on_change)
        layout.addWidget(self.changeButton)

        # --- Entsperren ------------------------------------------------
        layout.addWidget(StrongBodyLabel("User-PIN entsperren", self))
        layout.addWidget(BodyLabel(
            "Funktioniert mit dem SO-PIN jederzeit — auch bei bereits "
            "gesperrter User-PIN (anders als Ändern).", self,
        ))
        self.soPinEdit = _pin_field(self, "soPinEdit")
        self.soPinEdit.setPlaceholderText("SO-PIN")
        self.unblockNewPinEdit1 = _pin_field(self, "unblockNewPinEdit1")
        self.unblockNewPinEdit1.setPlaceholderText("Neue User-PIN")
        self.unblockNewPinEdit2 = _pin_field(self, "unblockNewPinEdit2")
        self.unblockNewPinEdit2.setPlaceholderText("Neue User-PIN bestätigen")
        layout.addWidget(self.soPinEdit)
        layout.addWidget(self.unblockNewPinEdit1)
        layout.addWidget(self.unblockNewPinEdit2)
        self.unblockButton = PrimaryPushButton("Zurücksetzen", self)
        self.unblockButton.setObjectName("unblockButton")
        self.unblockButton.clicked.connect(self._on_unblock)
        layout.addWidget(self.unblockButton)

        # --- Status ----------------------------------------------------
        status_head = QHBoxLayout()
        status_head.addWidget(StrongBodyLabel("PIN-Status", self))
        status_head.addStretch(1)
        self.pinRefreshButton = PushButton("Aktualisieren", self)
        self.pinRefreshButton.setObjectName("pinRefreshButton")
        self.pinRefreshButton.clicked.connect(self.refresh)
        status_head.addWidget(self.pinRefreshButton)
        layout.addLayout(status_head)
        self.pinStatusLabel = BodyLabel("Noch nicht abgefragt.", self)
        self.pinStatusLabel.setObjectName("pinStatusLabel")
        layout.addWidget(self.pinStatusLabel)

        layout.addStretch(0)

    # --- Laden -----------------------------------------------------------

    def refresh(self) -> None:
        """PIN-Status neu abfragen (Button + Tab-Wechsel)."""
        close_info_bars(self._info_bars)

        self.pinRefreshButton.setEnabled(False)
        self._worker = FunctionWorker(_query_pin_flags)
        self._worker.signals.finished.connect(self._on_flags_finished)
        self._worker.signals.error.connect(self._on_error)
        QThreadPool.globalInstance().start(self._worker)

    def _show_error(self, text: str) -> None:
        bar = InfoBar.error(
            "Fehler", text, parent=self,
            position=InfoBarPosition.TOP, duration=-1,
        )
        self._info_bars.append(bar)

    def _show_warning(self, text: str) -> None:
        bar = InfoBar.warning(
            "Hinweis", text, parent=self,
            position=InfoBarPosition.TOP, duration=-1,
        )
        self._info_bars.append(bar)

    def _show_success(self, text: str) -> None:
        bar = InfoBar.success(
            "Erfolg", text, parent=self,
            position=InfoBarPosition.TOP, duration=5000,
        )
        self._info_bars.append(bar)

    def _on_flags_finished(self, result: dict[str, Any]) -> None:
        self.pinRefreshButton.setEnabled(True)
        self._worker = None

        flags = result["flags"]
        if flags is None:
            self.pinStatusLabel.setText("Nicht lesbar.")
        else:
            lines = [f"  {key}: {value}" for key, value in flags.items()]
            self.pinStatusLabel.setText("\n".join(lines))
            critical = [key for key in pc.CRITICAL_PIN_FLAGS if flags.get(key)]
            if critical:
                self._show_warning(
                    "PIN-Status kritisch (" + ", ".join(critical) + ") — "
                    "ggf. oben entsperren."
                )

        if result["error"] is not None:
            self._show_error(result["error"])

    def _on_error(self, exc: Exception) -> None:
        """Sicherheitsnetz (eigentlich fängt _query alles ab)."""
        self.pinRefreshButton.setEnabled(True)
        self._worker = None
        self._show_error(f"Unerwarteter Fehler ({exc}).")

    # --- Schreiben ---------------------------------------------------------

    @staticmethod
    def _read_pin_fields(*fields: PasswordLineEdit) -> list[str] | None:
        """Feldwerte lesen; None bei leerem Feld (UI meldet selbst)."""
        values = [field.text() for field in fields]
        return values if all(values) else None

    def _clear_pin_fields(self, *fields: PasswordLineEdit) -> None:
        """PIN-Felder nach Gebrauch leeren (§8)."""
        for field in fields:
            field.setText("")

    def _on_change(self) -> None:
        from gui.pin_vault import vault

        old_pin = vault.get()
        if not old_pin:
            self._show_error("Gesperrt — bitte zuerst anmelden (Start-Tab).")
            return
        values = self._read_pin_fields(
            self.newPinEdit1, self.newPinEdit2,
        )
        if values is None:
            self._show_error("Beide neuen PIN-Felder ausfüllen.")
            return
        new_pin, confirm_pin = values
        if new_pin != confirm_pin:
            self._show_error("Neue PINs stimmen nicht überein.")
            return
        self.changeButton.setEnabled(False)
        worker = FunctionWorker(_do_change, old_pin, new_pin)
        worker.signals.finished.connect(self._on_change_finished)
        worker.signals.error.connect(self._on_change_failed)
        self._worker = worker
        QThreadPool.globalInstance().start(worker)

    def _on_change_finished(self, _result: None) -> None:
        from gui.pin_vault import vault

        self.changeButton.setEnabled(True)
        self._worker = None
        vault.unlock(self.newPinEdit1.text())
        self._clear_pin_fields(
            self.newPinEdit1, self.newPinEdit2,
        )
        self._show_success("PIN erfolgreich geändert.")

    def _on_change_failed(self, exc: Exception) -> None:
        self.changeButton.setEnabled(True)
        self._worker = None
        self._show_error(f"PIN-Änderung fehlgeschlagen ({exc}).")

    def _on_unblock(self) -> None:
        values = self._read_pin_fields(
            self.soPinEdit, self.unblockNewPinEdit1, self.unblockNewPinEdit2,
        )
        if values is None:
            self._show_error("Alle drei PIN-Felder ausfüllen.")
            return
        so_pin, new_pin, confirm_pin = values
        if new_pin != confirm_pin:
            self._show_error("Neue PINs stimmen nicht überein.")
            return
        self.unblockButton.setEnabled(False)
        worker = FunctionWorker(_do_unblock, so_pin, new_pin)
        worker.signals.finished.connect(self._on_unblock_finished)
        worker.signals.error.connect(self._on_unblock_failed)
        self._worker = worker
        QThreadPool.globalInstance().start(worker)

    def _on_unblock_finished(self, _result: None) -> None:
        from gui.pin_vault import vault

        self.unblockButton.setEnabled(True)
        self._worker = None
        vault.unlock(self.unblockNewPinEdit1.text())
        self._clear_pin_fields(
            self.soPinEdit, self.unblockNewPinEdit1, self.unblockNewPinEdit2,
        )
        self._show_success("PIN zurückgesetzt.")

    def _on_unblock_failed(self, exc: Exception) -> None:
        self.unblockButton.setEnabled(True)
        self._worker = None
        self._show_error(f"PIN-Unblock fehlgeschlagen ({exc}).")
