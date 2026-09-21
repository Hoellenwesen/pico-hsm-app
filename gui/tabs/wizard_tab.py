"""wizard_tab.py — Einrichtungs-Assistent (geführter Erst-Workflow).

Vier Seiten im QStackedWidget: Board erkennen, Token initialisieren,
PIN ändern, DKEK-Status, danach Fertig. Backup läuft bewusst NICHT
hier (dafür Backup-Tab + HSM-Backup, inkl. Drill und Hygiene).
Jede Seite hat eine Aktion; „Weiter" ist erst nach Erfolg
freigeschaltet (kein Überspringen). Fortschritt liegt in
`~/.pico_hsm/setup_state.json` — der Wizard öffnet beim Start auf dem
ersten unerledigten Schritt.

Wiederverwendet Core-Funktionen der Tabs (keine Logik-Duplikate):
device_mode.detect, init_core.initialize_token, pin_core.change_user_pin,
dkek_core.dkek_status, backup_core.split_backup/real_drill (Empfänger-
Modus). DKEK-Shares anlegen/einspielen bleibt CLI-only (Custodian-TTY,
siehe DKEK-Tab) — der Wizard verweist nur dorthin. Alle langen Calls
laufen im FunctionWorker, nie im UI-Thread.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import (
    QHBoxLayout,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CompactSpinBox,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PasswordLineEdit,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    TextEdit,
    TitleLabel,
)

from gui.session_helpers import close_info_bars, confirm_destructive
from gui.workers import FunctionWorker
from pico_hsm_tools import dkek_core as dc
from pico_hsm_tools import init_core as ic
from pico_hsm_tools import pin_core as pc
from pico_hsm_tools.device_mode import MODE_LABEL, DeviceMode, detect
from pico_hsm_tools.paths import base_dir

STATE_FILE = base_dir() / "setup_state.json"

STEP_KEYS = ("detect", "init", "pin", "dkek")
STEP_TITLES = (
    "1/4 · Board erkennen",
    "2/4 · Token initialisieren",
    "3/4 · PIN ändern",
    "4/4 · DKEK-Status",
)


def state_file_for(serial: str | None) -> Path:
    """State-Datei je Board (Serial sicher eingebettet), Fallback global."""
    if serial:
        safe = "".join(
            c if c.isalnum() or c in "-_" else "_" for c in serial
        )[:64] or "unbekannt"
        return STATE_FILE.with_name(f"setup_state_{safe}.json")
    return STATE_FILE


def load_state(serial: str | None = None) -> dict[str, bool]:
    """Gespeicherten Fortschritt laden (fehlend/korrupt = von vorn)."""
    try:
        data = json.loads(state_file_for(serial).read_text(encoding="utf-8"))
        done = data.get("done", {})
        return {key: bool(done.get(key, False)) for key in STEP_KEYS}
    except Exception:  # noqa: BLE001 — kein State ist normal
        return {key: False for key in STEP_KEYS}


def save_state(done: dict[str, bool], serial: str | None = None) -> None:
    """Fortschritt speichern (Fehler sind nicht kritisch)."""
    try:
        target = state_file_for(serial)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps({"done": done}, indent=2), encoding="utf-8",
        )
    except Exception:  # noqa: BLE001 — Setup läuft auch ohne State-Datei
        pass


def board_completed(serial: str | None = None) -> dict[str, bool]:
    """Board-abgeleiteter Fortschritt (ohne Login lesbar).

    init: user_pin_initialized-Flag. pin: bei eingerichtetem Board
    optional (kein Nachweis nötig). detect/dkek: nicht board-seitig
    erkennbar (bleiben Datei-State). Fehler (kein Board) = alles offen.
    """
    done = {key: False for key in STEP_KEYS}
    try:
        flags = pc.read_pin_flags(serial=serial)
    except Exception:  # noqa: BLE001 — kein Board lesbar
        return done
    initialized = bool(flags.get("user_pin_initialized", False))
    done["init"] = initialized
    done["pin"] = initialized
    return done


def merged_state(serial: str | None = None) -> dict[str, bool]:
    """Datei- und Board-State vereinen (Board-Wahrheit gewinnt nie
    rückgängig: True aus einer Quelle reicht)."""
    file_state = load_state(serial)
    board_state = board_completed(serial)
    return {
        key: bool(file_state.get(key, False) or board_state.get(key, False))
        for key in STEP_KEYS
    }


def first_open_step(done: dict[str, bool]) -> int:
    """Index des ersten unerledigten Schritts (4 = alle fertig)."""
    for index, key in enumerate(STEP_KEYS):
        if not done.get(key, False):
            return index
    return len(STEP_KEYS)


def _configured_serial() -> str | None:
    """Token-Auswahl aus der GUI-Config (Login/Status-Dropdown)."""
    from gui import config as gui_config

    value = gui_config.cfg.tokenSerial.value.strip()
    return value or None


class WizardTab(QWidget):
    """Wizard-Tab: geführte Ersteinrichtung in 5 + 1 Seiten."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("wizard")
        self._info_bars: list[InfoBar] = []
        self._worker: FunctionWorker | None = None
        self._pending_handler: Any = None
        self._serial = _configured_serial()
        self._done = merged_state(self._serial)
        save_state(self._done, self._serial)

        layout = QVBoxLayout(self)
        layout.addWidget(TitleLabel("Einrichtung", self))
        layout.addWidget(BodyLabel(
            "Geführte Ersteinrichtung — Schritt für Schritt, "
            "jeder Schritt erst nach Erfolg des vorherigen.",
            self,
        ))

        self.pages = QStackedWidget(self)
        self.pages.setObjectName("wizardPages")
        self._build_detect_page()
        self._build_init_page()
        self._build_pin_page()
        self._build_dkek_page()
        self._build_done_page()
        layout.addWidget(self.pages, 1)

        nav_row = QHBoxLayout()
        self.backButton = PushButton("Zurück", self)
        self.backButton.setObjectName("wizardBackButton")
        self.backButton.clicked.connect(self._on_back)
        self.nextButton = PrimaryPushButton("Weiter", self)
        self.nextButton.setObjectName("wizardNextButton")
        self.nextButton.clicked.connect(self._on_next)
        nav_row.addWidget(self.backButton)
        nav_row.addStretch(1)
        nav_row.addWidget(self.nextButton)
        layout.addLayout(nav_row)

        self._goto(first_open_step(self._done))

    # --- Seitenaufbau ------------------------------------------------------

    def _page_shell(self, title: str) -> QVBoxLayout:
        page = QWidget(self.pages)
        page_layout = QVBoxLayout(page)
        page_layout.addWidget(StrongBodyLabel(title, page))
        self.pages.addWidget(page)
        return page_layout

    def _build_detect_page(self) -> None:
        box = self._page_shell(STEP_TITLES[0])
        box.addWidget(BodyLabel(
            "Board per USB verbinden (Normal-Modus reicht für diesen "
            "Schritt — BOOTSEL ist erst für Firmware/OTP nötig).", self,
        ))
        self.detectButton = PrimaryPushButton("Board erkennen", self)
        self.detectButton.setObjectName("wizardDetectButton")
        self.detectButton.clicked.connect(self._on_detect)
        box.addWidget(self.detectButton)
        self.detectLabel = BodyLabel("Noch nicht erkannt.", self)
        self.detectLabel.setObjectName("wizardDetectLabel")
        box.addWidget(self.detectLabel)
        box.addStretch(1)

    def _build_init_page(self) -> None:
        box = self._page_shell(STEP_TITLES[1])
        box.addWidget(BodyLabel(
            "LÖSCHT alle Keys/Zertifikate/Dateien auf dem Token. "
            "SO-PIN: 16 Hex-Zeichen (Werkseinstellung je nach Karte).", self,
        ))
        self.soPinEdit = PasswordLineEdit(self)
        self.soPinEdit.setObjectName("wizardSoPinEdit")
        self.soPinEdit.setPlaceholderText("SO-PIN (16 Hex-Zeichen)")
        box.addWidget(self.soPinEdit)
        self.initPinEdit1 = PasswordLineEdit(self)
        self.initPinEdit1.setObjectName("wizardInitPinEdit1")
        self.initPinEdit1.setPlaceholderText("Neue User-PIN")
        box.addWidget(self.initPinEdit1)
        self.initPinEdit2 = PasswordLineEdit(self)
        self.initPinEdit2.setObjectName("wizardInitPinEdit2")
        self.initPinEdit2.setPlaceholderText("Neue User-PIN bestätigen")
        box.addWidget(self.initPinEdit2)
        shares_row = QHBoxLayout()
        shares_row.addWidget(BodyLabel("DKEK-Shares (0 = zufällig/lokal):", self))
        self.initSharesSpin = CompactSpinBox(self)
        self.initSharesSpin.setObjectName("wizardInitSharesSpin")
        self.initSharesSpin.setRange(0, 255)
        self.initSharesSpin.setValue(0)
        shares_row.addWidget(self.initSharesSpin)
        shares_row.addStretch(1)
        box.addLayout(shares_row)
        self.initButton = PrimaryPushButton("Token initialisieren", self)
        self.initButton.setObjectName("wizardInitButton")
        self.initButton.clicked.connect(self._on_init)
        box.addWidget(self.initButton)
        box.addStretch(1)

    def _build_pin_page(self) -> None:
        box = self._page_shell(STEP_TITLES[2])
        box.addWidget(BodyLabel(
            "User-PIN wechseln (Nachweis, dass die PIN aus Schritt 2 "
            "sitzt). Felder werden nach Erfolg geleert.", self,
        ))
        self.oldPinEdit = PasswordLineEdit(self)
        self.oldPinEdit.setObjectName("wizardOldPinEdit")
        self.oldPinEdit.setPlaceholderText("Aktuelle User-PIN")
        box.addWidget(self.oldPinEdit)
        self.newPinEdit1 = PasswordLineEdit(self)
        self.newPinEdit1.setObjectName("wizardNewPinEdit1")
        self.newPinEdit1.setPlaceholderText("Neue User-PIN")
        box.addWidget(self.newPinEdit1)
        self.newPinEdit2 = PasswordLineEdit(self)
        self.newPinEdit2.setObjectName("wizardNewPinEdit2")
        self.newPinEdit2.setPlaceholderText("Neue User-PIN bestätigen")
        box.addWidget(self.newPinEdit2)
        self.pinChangeButton = PrimaryPushButton("PIN ändern", self)
        self.pinChangeButton.setObjectName("wizardPinChangeButton")
        self.pinChangeButton.clicked.connect(self._on_pin_change)
        box.addWidget(self.pinChangeButton)
        box.addStretch(1)

    def _build_dkek_page(self) -> None:
        box = self._page_shell(STEP_TITLES[3])
        box.addWidget(BodyLabel(
            "DKEK-Status abfragen. Shares anlegen/einspielen bleibt "
            "CLI-only (`dkek create-share`/`import-share`, Custodian-TTY) "
            "— siehe DKEK-Tab.", self,
        ))
        self.dkekQueryButton = PrimaryPushButton("DKEK-Status abfragen", self)
        self.dkekQueryButton.setObjectName("wizardDkekQueryButton")
        self.dkekQueryButton.clicked.connect(self._on_dkek_query)
        box.addWidget(self.dkekQueryButton)
        self.dkekStatusText = TextEdit(self)
        self.dkekStatusText.setObjectName("wizardDkekStatusText")
        self.dkekStatusText.setReadOnly(True)
        self.dkekStatusText.setPlaceholderText("DKEK-Status")
        self.dkekStatusText.setMaximumHeight(140)
        box.addWidget(self.dkekStatusText)
        box.addStretch(1)

    def _build_done_page(self) -> None:
        box = self._page_shell("Fertig")
        self.doneLabel = BodyLabel("", self)
        self.doneLabel.setObjectName("wizardDoneLabel")
        self.doneLabel.setWordWrap(True)
        box.addWidget(self.doneLabel)
        jump_row = QHBoxLayout()
        self.gotoStatusButton = PushButton("Zum Status-Tab", self)
        self.gotoStatusButton.setObjectName("wizardGotoStatusButton")
        self.gotoStatusButton.clicked.connect(lambda: self._goto_tab("status"))
        self.gotoBackupButton = PushButton("Zum Backup-Tab", self)
        self.gotoBackupButton.setObjectName("wizardGotoBackupButton")
        self.gotoBackupButton.clicked.connect(lambda: self._goto_tab("backup"))
        self.restartButton = PushButton("Von vorn", self)
        self.restartButton.setObjectName("wizardRestartButton")
        self.restartButton.clicked.connect(self._on_restart)
        jump_row.addWidget(self.gotoStatusButton)
        jump_row.addWidget(self.gotoBackupButton)
        jump_row.addStretch(1)
        jump_row.addWidget(self.restartButton)
        box.addLayout(jump_row)
        box.addStretch(1)

    # --- Navigation --------------------------------------------------------

    def _open_indices(self) -> list[int]:
        """Nur unerledigte Schritte stehen zur Auswahl."""
        return [
            index for index, key in enumerate(STEP_KEYS)
            if not self._done.get(key, False)
        ]

    def _goto(self, index: int) -> None:
        index = max(0, min(index, len(STEP_KEYS)))
        self.pages.setCurrentIndex(index)
        opens = self._open_indices()
        self.backButton.setEnabled(
            any(i < index for i in opens) or index >= len(STEP_KEYS)
        )
        if index >= len(STEP_KEYS):
            self.nextButton.setEnabled(False)
            done_count = sum(1 for key in STEP_KEYS if self._done.get(key))
            self.doneLabel.setText(
                f"Ersteinrichtung abgeschlossen ({done_count}/{len(STEP_KEYS)} "
                "Schritte). Backup-Datei an alle Standorte verteilen, "
                "Drill-Ergebnis im Backup-Tab prüfen."
            )
        else:
            key = STEP_KEYS[index]
            self.nextButton.setEnabled(bool(self._done.get(key, False)))

    def _mark_done(self, key: str) -> None:
        self._done[key] = True
        save_state(self._done, self._serial)
        self._goto(self.pages.currentIndex())

    def _on_back(self) -> None:
        current = self.pages.currentIndex()
        previous = [i for i in self._open_indices() if i < current]
        if previous:
            self._goto(previous[-1])
        # Sonst bleiben (erledigte Schritte stehen nicht zur Auswahl).

    def _on_next(self) -> None:
        current = self.pages.currentIndex()
        following = [i for i in self._open_indices() if i > current]
        self._goto(following[0] if following else len(STEP_KEYS))

    def _on_restart(self) -> None:
        self._done = {key: False for key in STEP_KEYS}
        save_state(self._done, self._serial)
        self._goto(0)

    def _goto_tab(self, route: str) -> None:
        show_tab = getattr(self.window(), "show_tab", None)
        if callable(show_tab):
            show_tab(route)

    def apply_device_mode(self, mode: object, _state: object = None) -> None:
        """Wizard ist in jedem Modus geöffnet (Schritte prüfen selbst)."""

    def refresh(self) -> None:
        """Beim Tab-Wechsel: Board-State neu vereinen (kein Sprung —
        Arbeit läuft weiter, nur Weiter/Zurück-Aktivierung passt sich an)."""
        self._serial = _configured_serial()
        self._done = merged_state(self._serial)
        self._goto(self.pages.currentIndex())

    # --- InfoBars ------------------------------------------------------------

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

    def _clear_bars(self) -> None:
        close_info_bars(self._info_bars)

    def _start_worker(self, fn: Any, *args: Any) -> None:
        worker = FunctionWorker(fn, *args)
        worker.signals.finished.connect(self._on_worker_finished)
        worker.signals.error.connect(self._on_worker_failed)
        self._worker = worker
        QThreadPool.globalInstance().start(worker)

    def _on_worker_finished(self, result: Any) -> None:
        self._worker = None
        handler = getattr(self, "_pending_handler", None)
        self._pending_handler = None
        if callable(handler):
            handler(result)

    def _on_worker_failed(self, exc: Exception) -> None:
        self._worker = None
        self._pending_handler = None
        self._show_error(str(exc))
        self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        for button in (
            self.detectButton, self.initButton, self.pinChangeButton,
            self.dkekQueryButton,
        ):
            button.setEnabled(not busy)

    # --- Schritt 1: Board erkennen -------------------------------------------

    def _on_detect(self) -> None:
        self._clear_bars()
        self._set_busy(True)
        self._pending_handler = self._handle_detected
        self._start_worker(detect)

    def _handle_detected(self, result: Any) -> None:
        self._set_busy(False)
        mode = result.mode if hasattr(result, "mode") else DeviceMode.KEIN_GERAET
        label = MODE_LABEL.get(mode, str(mode))
        if mode == DeviceMode.KEIN_GERAET:
            self.detectLabel.setText(f"Kein Board erkannt ({result.detail}).")
            self._show_error(
                "Kein Board erkannt — USB prüfen (Normal reicht hier)."
            )
            return
        self.detectLabel.setText(f"Board erkannt (Modus: {label}).")
        self._show_success(f"Board erkannt (Modus: {label}).")
        self._mark_done("detect")

    # --- Schritt 2: Token initialisieren -------------------------------------

    def _on_init(self) -> None:
        self._clear_bars()
        so_pin = self.soPinEdit.text().strip()
        pin1 = self.initPinEdit1.text()
        pin2 = self.initPinEdit2.text()
        if len(so_pin) != 16 or any(c not in "0123456789abcdefABCDEF" for c in so_pin):
            self._show_error("SO-PIN muss genau 16 Hex-Zeichen haben.")
            return
        if not pin1 or pin1 != pin2:
            self._show_error("User-PINs stimmen nicht überein (oder leer).")
            return
        if not confirm_destructive(
            self, "Token initialisieren",
            "LÖSCHT alle Keys/Zertifikate/Dateien auf dem Token "
            "unwiderruflich. Wirklich initialisieren?",
        ):
            return
        shares = self.initSharesSpin.value()
        self._set_busy(True)
        self._pending_handler = self._handle_initialized
        self._start_worker(
            ic.initialize_token, so_pin, pin1,
            shares if shares > 0 else None,
        )

    def _handle_initialized(self, _result: Any) -> None:
        self._set_busy(False)
        self.soPinEdit.setText("")
        self.initPinEdit1.setText("")
        self.initPinEdit2.setText("")
        self._show_success("Token initialisiert.")
        self._mark_done("init")

    # --- Schritt 3: PIN ändern -------------------------------------------------

    def _on_pin_change(self) -> None:
        self._clear_bars()
        old = self.oldPinEdit.text()
        new1 = self.newPinEdit1.text()
        new2 = self.newPinEdit2.text()
        if not new1 or new1 != new2:
            self._show_error("Neue PINs stimmen nicht überein (oder leer).")
            return
        self._set_busy(True)
        self._pending_handler = self._handle_pin_changed
        from gui.session_helpers import selected_serial

        self._start_worker(
            pc.change_user_pin, old, new1, None, selected_serial(),
        )

    def _handle_pin_changed(self, _result: Any) -> None:
        self._set_busy(False)
        self.oldPinEdit.setText("")
        self.newPinEdit1.setText("")
        self.newPinEdit2.setText("")
        self._show_success("PIN geändert.")
        self._mark_done("pin")

    # --- Schritt 4: DKEK-Status --------------------------------------------------

    def _on_dkek_query(self) -> None:
        self._clear_bars()
        self._set_busy(True)
        self._pending_handler = self._handle_dkek_status
        self._start_worker(dc.dkek_status)

    def _handle_dkek_status(self, result: Any) -> None:
        self._set_busy(False)
        self.dkekStatusText.setPlainText(str(result))
        self._show_success("DKEK-Status abgefragt.")
        self._mark_done("dkek")
