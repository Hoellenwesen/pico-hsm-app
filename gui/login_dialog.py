"""login_dialog.py — Anmelde-Dialog beim App-Start (Baustein B).

Modal vor dem MainWindow: Board aus Liste wählen, dann je Zustand —
neues Board (User-PIN nicht eingerichtet) -> „Assistent öffnen";
bekanntes Board -> User-PIN + „Entsperren" (Verifikation per
read_only_session im Worker); immer möglich: „Nur ansehen" (ohne
PIN; PIN-pflichtige Aktionen melden dann „Gesperrt").

Ergebnis: LoginResult(authenticated, serial, open_wizard). Die PIN
selbst landet NICHT im Result — Entsperren schreibt direkt in den
Vault (RAM only). BOOTSEL-Boards (kein Token) erscheinen als Hinweis.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QDialog, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    InfoBar,
    InfoBarPosition,
    PasswordLineEdit,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    TitleLabel,
)

from gui.workers import FunctionWorker
from pico_hsm_tools import pin_core as pc
from pico_hsm_tools.pkcs11_session import list_tokens, read_only_session


@dataclass
class LoginResult:
    authenticated: bool = False
    serial: str | None = None
    open_wizard: bool = False


class LoginDialog(QDialog):
    """Start-Dialog: Board wählen, anmelden oder nur ansehen."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("loginDialog")
        self.setWindowTitle("Pico HSM — Anmeldung")
        self.resize(480, 420)
        self.result = LoginResult()
        self._worker: FunctionWorker | None = None
        self._pending_pin: str | None = None
        self._pending_serial: str | None = None
        self._info_bars: list[InfoBar] = []

        layout = QVBoxLayout(self)
        layout.addWidget(TitleLabel("Anmeldung", self))
        layout.addWidget(BodyLabel(
            "Board wählen und entsperren — oder bei neuem Board direkt "
            "in den Assistenten.",
            self,
        ))

        # --- Board-Liste --------------------------------------------------
        layout.addWidget(StrongBodyLabel("Board", self))
        board_row = QHBoxLayout()
        self.boardCombo = ComboBox(self)
        self.boardCombo.setObjectName("boardCombo")
        self.boardCombo.setPlaceholderText("Board wählen")
        self.boardRefreshButton = PushButton("Suchen", self)
        self.boardRefreshButton.setObjectName("boardRefreshButton")
        self.boardRefreshButton.clicked.connect(self.refresh_boards)
        board_row.addWidget(self.boardCombo, 1)
        board_row.addWidget(self.boardRefreshButton)
        layout.addLayout(board_row)
        self.bootselHintLabel = BodyLabel("", self)
        self.bootselHintLabel.setObjectName("bootselHintLabel")
        self.bootselHintLabel.setWordWrap(True)
        layout.addWidget(self.bootselHintLabel)
        self.nextButton = PrimaryPushButton("Weiter", self)
        self.nextButton.setObjectName("loginNextButton")
        self.nextButton.clicked.connect(self._on_next)
        layout.addWidget(self.nextButton)
        self.statusLabel = BodyLabel("", self)
        self.statusLabel.setObjectName("loginStatusLabel")
        self.statusLabel.setWordWrap(True)
        layout.addWidget(self.statusLabel)

        # --- Neues Board ---------------------------------------------------
        self.newBoardBox = QWidget(self)
        self.newBoardBox.setObjectName("newBoardBox")
        new_layout = QVBoxLayout(self.newBoardBox)
        new_layout.setContentsMargins(0, 0, 0, 0)
        new_layout.addWidget(BodyLabel(
            "Neues, leeres Board (keine User-PIN eingerichtet). "
            "Einrichtung nur über den Assistenten.", self,
        ))
        self.wizardButton = PrimaryPushButton("Assistent öffnen", self)
        self.wizardButton.setObjectName("wizardButton")
        self.wizardButton.clicked.connect(self._on_wizard)
        new_layout.addWidget(self.wizardButton)
        layout.addWidget(self.newBoardBox)

        # --- PIN ------------------------------------------------------------
        self.pinBox = QWidget(self)
        self.pinBox.setObjectName("pinBox")
        pin_layout = QVBoxLayout(self.pinBox)
        pin_layout.setContentsMargins(0, 0, 0, 0)
        pin_layout.addWidget(BodyLabel("User-PIN eingeben:", self))
        pin_row = QHBoxLayout()
        self.pinEdit = PasswordLineEdit(self)
        self.pinEdit.setObjectName("loginPinEdit")
        self.pinEdit.setPlaceholderText("User-PIN")
        self.unlockButton = PrimaryPushButton("Entsperren", self)
        self.unlockButton.setObjectName("unlockButton")
        self.unlockButton.clicked.connect(self._on_unlock)
        pin_row.addWidget(self.pinEdit, 1)
        pin_row.addWidget(self.unlockButton)
        pin_layout.addLayout(pin_row)
        layout.addWidget(self.pinBox)

        # --- Fußzeile ---------------------------------------------------------
        foot_row = QHBoxLayout()
        foot_row.addStretch(1)
        self.cancelButton = PushButton("Beenden", self)
        self.cancelButton.setObjectName("loginCancelButton")
        self.cancelButton.clicked.connect(self.reject)
        foot_row.addWidget(self.cancelButton)
        layout.addLayout(foot_row)

        self.refresh_boards()
        self._show_step("board")

    # --- Schritte ------------------------------------------------------------

    def _show_step(self, step: str) -> None:
        """Sichtbarkeit je Schritt (board/neu/pin). Fußzeile bleibt."""
        self.newBoardBox.setVisible(step == "neu")
        self.pinBox.setVisible(step == "pin")

    def _show_error(self, text: str) -> None:
        bar = InfoBar.error(
            "Fehler", text, parent=self,
            position=InfoBarPosition.TOP, duration=-1,
        )
        self._info_bars.append(bar)

    def _clear_bars(self) -> None:
        for bar in self._info_bars:
            bar.close()
        self._info_bars.clear()

    # --- Board-Liste -----------------------------------------------------------

    def refresh_boards(self) -> None:
        """Token-Liste neu laden + BOOTSEL-Hinweis (Button + Start)."""
        self._clear_bars()
        current = self.boardCombo.currentData() or ""
        self.boardCombo.blockSignals(True)
        try:
            self.boardCombo.clear()
            try:
                tokens = list_tokens()
            except Exception as exc:  # noqa: BLE001 — leere Liste ist ok
                tokens = []
                self._show_error(f"Token-Liste nicht lesbar ({exc}).")
            for token in tokens:
                serial = str(token.get("serial", ""))
                label = str(token.get("label", ""))
                self.boardCombo.addItem(
                    f"{label} ({serial})" if label else serial,
                    userData=serial,
                )
            index = max(0, self.boardCombo.findData(current))
            self.boardCombo.setCurrentIndex(index)
            if not tokens:
                self.statusLabel.setText(
                    "Kein Board als Token erkannt — USB prüfen "
                    "(Normal-Modus) oder Assistent für BOOTSEL-Flash nutzen."
                )
            else:
                self.statusLabel.setText(
                    f"{len(tokens)} Board(s) erkannt — wählen und Weiter."
                )
        finally:
            self.boardCombo.blockSignals(False)
        self.bootselHintLabel.setText(self._bootsel_hint())

    @staticmethod
    def _bootsel_hint() -> str:
        """Hinweis bei BOOTSEL-Board (kein Token, nur picotool-sichtbar)."""
        try:
            from pico_hsm_tools import flash_core as fc

            fc.check_picotool_available()
            fc.is_secure_boot_enabled()
            return (
                "Hinweis: Board im BOOTSEL-Modus erkannt "
                "(Firmware/OTP-Modus — Anmeldung erst im Normal-Modus)."
            )
        except Exception:  # noqa: BLE001 — kein BOOTSEL-Board / kein Tool
            return ""

    # --- Weiter / Panels ---------------------------------------------------------

    def _selected_serial(self) -> str | None:
        return self.boardCombo.currentData() or None

    def _on_next(self) -> None:
        """Board-Zustand prüfen: neu -> Assistent-Panel, sonst PIN-Panel."""
        self._clear_bars()
        serial = self._selected_serial()
        if not serial:
            self._show_error("Bitte zuerst ein Board wählen (Suchen).")
            return
        try:
            flags = pc.read_pin_flags(serial=serial)
        except Exception as exc:  # noqa: BLE001 — als InfoBar
            self._show_error(f"Board nicht lesbar ({exc}).")
            return
        self._pending_serial = serial
        if not flags.get("user_pin_initialized", True):
            self.statusLabel.setText("Neues Board erkannt.")
            self._show_step("neu")
        else:
            problems = []
            if flags.get("user_pin_locked"):
                problems.append(
                    "User-PIN gesperrt — mit SO-PIN im PIN-Tab "
                    "entsperren (dort ohne Anmeldung möglich)."
                )
            self.statusLabel.setText(
                "Board bereit. " + " ".join(problems)
                if problems else "Board bereit — PIN eingeben."
            )
            self._show_step("pin")

    # --- Entsperren ---------------------------------------------------------------

    def _on_unlock(self) -> None:
        pin = self.pinEdit.text()
        if not pin:
            self._show_error("User-PIN eingeben.")
            return
        serial = self._pending_serial or self._selected_serial()
        if not serial:
            self._show_error("Bitte zuerst ein Board wählen.")
            return
        self._pending_pin = pin
        self._pending_serial = serial
        self.unlockButton.setEnabled(False)
        worker = FunctionWorker(
            _verify_login, pin, serial,
        )
        worker.signals.finished.connect(self._on_verified)
        worker.signals.error.connect(self._on_verify_failed)
        self._worker = worker
        QThreadPool.globalInstance().start(worker)

    def _on_verified(self, _result: object) -> None:
        from gui.pin_vault import vault

        self._worker = None
        self.unlockButton.setEnabled(True)
        assert self._pending_pin is not None
        vault.unlock(self._pending_pin)
        self.pinEdit.setText("")
        self._pending_pin = None
        self.result = LoginResult(
            authenticated=True, serial=self._pending_serial,
            open_wizard=False,
        )
        self.accept()

    def _on_verify_failed(self, exc: Exception) -> None:
        self._worker = None
        self.unlockButton.setEnabled(True)
        self._pending_pin = None
        self._show_error(f"Anmeldung fehlgeschlagen ({exc}).")

    # --- Ausgänge ---------------------------------------------------------------------

    def _on_wizard(self) -> None:
        self.result = LoginResult(
            authenticated=False, serial=self._pending_serial,
            open_wizard=True,
        )
        self.accept()


def _verify_login(pin: str, serial: str | None) -> bool:
    """PIN per lesender Session prüfen (Worker-Thread). True oder Wurf."""
    with read_only_session(user_pin=pin, serial=serial):
        return True
