"""status_tab.py — Status-Bereich (kompakt: Gerät + PIN-Status).

Zwei Sektionen, optisch per Trennlinie getrennt: Gerät (read-only, kein
PKCS#11-Lock, mit Token-Dropdown bei mehreren Geräten), PIN-Status aus
der TokenFlag-Bitmaske (ebenfalls ohne Login/Lock, siehe
pin_core.read_pin_flags). Das Audit-Log wohnt im Logs-Tab (eine Wahrheit,
keine Duplikate). Genau EIN FunctionWorker pro Refresh sammelt alles;
Fehler pro Sektion blockieren einander nicht und erscheinen als inline
InfoBar (kein Dialog bei Routine-Abfragen — getroffene Entscheidung).

Tab-Wechsel-Refresh läuft über MainWindow (currentChanged -> refresh(),
Duck-Typing-Muster). Auto-Refresh alle 60s nur wenn sichtbar.

Hinweis: Diese App und das HSM-API-Gateway laufen auf getrennten
Systemen — es gibt daher weder Erreichbarkeits-Check noch
Gateway-Anzeige (Kollisions-Check sauber entfernt).
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QThreadPool, QTimer
from PySide6.QtWidgets import QHBoxLayout, QTableWidgetItem, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    HorizontalSeparator,
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    TableWidget,
    TitleLabel,
)

from gui import config as gui_config
from gui.session_helpers import close_info_bars
from gui.workers import FunctionWorker
from pico_hsm_tools import audit_log
from pico_hsm_tools import backup_index
from pico_hsm_tools import dashboard as dash_mod
from pico_hsm_tools import pin_core as pc
from pico_hsm_tools.paths import base_dir
from pico_hsm_tools.pkcs11_session import (
    format_token_serial,
    get_token,
    list_tokens,
)

#: Standard-Backupordner (Wizard-Ziel) für die Dashboard-Hygiene.
#: Andere Orte deckt der Backup-Tab selbst ab.
DEFAULT_BACKUP_DIR = base_dir() / "backups"

PIN_FLAG_LABELS: tuple[tuple[str, str], ...] = (
    ("login_required", "Login erforderlich"),
    ("user_pin_initialized", "Benutzer-PIN eingerichtet"),
    ("user_pin_count_low", "Wenig Restversuche"),
    ("user_pin_final_try", "Letzter Versuch"),
    ("user_pin_locked", "PIN gesperrt"),
    ("user_pin_to_be_changed", "PIN-Änderung fällig"),
)


def _query_status(serial: str | None) -> dict[str, Any]:
    """Alle Sektionen einsammeln (läuft im Worker-Thread).

    Gibt dict mit device-Dict/None, pin-Flags-Dict/None, Token-Liste,
    Chain-Status und einer Fehlerliste zurück — ein fehlender Board
    blockiert nichts. Audit-Details gehören in den Logs-Tab.
    """
    errors: list[str] = []
    try:
        token = get_token(serial=serial)
        device: dict[str, Any] | None = {
            "label": token.label,
            "model": token.model,
            "serial": format_token_serial(token.serial),
        }
    except Exception as exc:  # noqa: BLE001 — als InfoBar, kein Abbruch
        device = None
        errors.append(f"Kein Gerät erkannt ({exc}).")

    try:
        pin_flags: dict[str, bool] | None = pc.read_pin_flags(serial=serial)
        pin_error: str | None = None
    except Exception as exc:  # noqa: BLE001 — als InfoBar, kein Abbruch
        pin_flags = None
        pin_error = f"PIN-Status nicht lesbar ({exc})."
        errors.append(pin_error)

    try:
        tokens = list_tokens()
    except Exception:  # noqa: BLE001 — leere Auswahl ist ok
        tokens = []

    try:
        chain_intact: bool | None = audit_log.audit_chain_intact()
    except Exception as exc:  # noqa: BLE001 — als InfoBar, kein Abbruch
        chain_intact = None
        errors.append(f"Audit-Chain nicht prüfbar ({exc}).")

    try:
        from gui.tabs import wizard_tab as wiz_mod

        setup_done: dict[str, bool] | None = wiz_mod.load_state()
    except Exception:  # noqa: BLE001 — ohne State kein Setup-Hinweis
        setup_done = None

    backup_warnings: list[str] = []
    try:
        for info in backup_index.list_backups(DEFAULT_BACKUP_DIR):
            backup_warnings.extend(backup_index.hygiene_warnings(info))
            if len(backup_warnings) >= 3:
                break
        backup_warnings = backup_warnings[:3]
    except Exception:  # noqa: BLE001 — ohne Backups kein Hygiene-Hinweis
        backup_warnings = []

    recommendations = dash_mod.collect(
        device_present=device is not None,
        pin_flags=pin_flags,
        chain_intact=chain_intact,
        setup_done=setup_done,
        backup_warnings=backup_warnings,
        critical_pin_flags=pc.CRITICAL_PIN_FLAGS,
        pin_labels=PIN_FLAG_LABELS,
    )

    return {
        "device": device,
        "pin_flags": pin_flags,
        "tokens": tokens,
        "recommendations": [
            {"text": reco.text, "route": reco.route}
            for reco in recommendations
        ],
        "errors": errors,
    }


class StatusTab(QWidget):
    """Status-Tab: Gerät, PIN-Status (kompakt)."""

    #: Auto-Refresh-Intervall (ms) — nur wenn der Tab sichtbar ist.
    AUTO_REFRESH_MS = 60_000

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("status")
        self._info_bars: list[InfoBar] = []
        self._worker: FunctionWorker | None = None
        self._auto = False
        self._last_bar_key: tuple[str, ...] | None = None
        self._auto_timer = QTimer(self)
        self._auto_timer.setInterval(self.AUTO_REFRESH_MS)
        self._auto_timer.timeout.connect(self._on_auto_timeout)

        layout = QVBoxLayout(self)
        layout.addWidget(TitleLabel("Status", self))

        # --- Empfehlungen (Dashboard) -----------------------------------
        layout.addWidget(StrongBodyLabel("Empfehlungen", self))
        self.recoBox = QVBoxLayout()
        self.recoBox.setObjectName("recoBox")
        reco_container = QWidget(self)
        reco_container.setObjectName("recoContainer")
        reco_container.setLayout(self.recoBox)
        layout.addWidget(reco_container)
        self._reco_rows: list[QWidget] = []

        layout.addWidget(HorizontalSeparator(self))

        # --- Gerät ------------------------------------------------------
        layout.addWidget(StrongBodyLabel("Gerät", self))
        device_row = QHBoxLayout()
        self.tokenCombo = ComboBox(self)
        self.tokenCombo.setObjectName("tokenCombo")
        self.tokenCombo.setPlaceholderText("Token wählen (leer = automatisch)")
        self.tokenCombo.addItem("Automatisch", "")
        saved = gui_config.cfg.tokenSerial.value.strip()
        if saved:
            self.tokenCombo.addItem(saved, userData=saved)
            self.tokenCombo.setCurrentIndex(1)
        device_row.addWidget(self.tokenCombo, 1)
        self.refreshButton = PrimaryPushButton("Aktualisieren", self)
        self.refreshButton.setObjectName("refreshButton")
        self.refreshButton.clicked.connect(self.refresh)
        device_row.addWidget(self.refreshButton)
        layout.addLayout(device_row)
        self.deviceLabel = BodyLabel("Noch nicht abgefragt.", self)
        self.deviceLabel.setObjectName("deviceLabel")
        layout.addWidget(self.deviceLabel)

        layout.addWidget(HorizontalSeparator(self))

        # --- PIN-Status (ohne Login, aus TokenFlag-Bitmaske) --------------
        layout.addWidget(StrongBodyLabel("PIN-Status", self))
        self.pinFlagsTable = TableWidget(self)
        self.pinFlagsTable.setObjectName("pinFlagsTable")
        self.pinFlagsTable.setColumnCount(2)
        self.pinFlagsTable.setHorizontalHeaderLabels(["Merkmal", "Wert"])
        self.pinFlagsTable.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        self.pinFlagsTable.setMaximumHeight(220)
        layout.addWidget(self.pinFlagsTable)

        layout.addStretch(0)

    def showEvent(self, event) -> None:  # noqa: N802 (Qt-Konvention)
        """Auto-Refresh starten, sobald der Tab sichtbar wird."""
        super().showEvent(event)
        self._auto_timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802 (Qt-Konvention)
        """Auto-Refresh stoppen, sobald der Tab verlassen wird."""
        self._auto_timer.stop()
        super().hideEvent(event)

    def _on_auto_timeout(self) -> None:
        """60s-Tick: nur nachladen, wenn kein Lauf aktiv ist."""
        if self._worker is None and self.isVisible():
            self.refresh(auto=True)

    # --- Refresh ---------------------------------------------------------

    def refresh(self, auto: bool = False) -> None:
        """Alle Sektionen neu abfragen (Button + Tab-Wechsel + Auto-Timer).

        Läuft bereits ein Worker, wird der Aufruf ignoriert (kein
        Doppel-Lauf). Auto-Ticks zeigen InfoBars nur bei geänderter
        Fehlerlage (kein Blinken im 60s-Takt); manuelle Refreshs
        zeigen immer.
        """
        if self._worker is not None:
            return
        self._auto = auto

        serial = self.tokenCombo.currentData() or None

        gui_config.cfg.tokenSerial.value = serial or ""
        gui_config.save_config()

        self.refreshButton.setEnabled(False)
        self._worker = FunctionWorker(_query_status, serial)
        self._worker.signals.finished.connect(self._on_finished)
        self._worker.signals.error.connect(self._on_error)
        QThreadPool.globalInstance().start(self._worker)

    def _fill_token_combo(self, tokens: list[dict]) -> None:
        """Dropdown mit erkannten Tokens füllen, Auswahl erhalten."""
        current = self.tokenCombo.currentData() or ""
        self.tokenCombo.blockSignals(True)
        try:
            self.tokenCombo.clear()
            self.tokenCombo.addItem("Automatisch", userData="")
            for token in tokens:
                serial = str(token.get("serial", ""))
                label = str(token.get("label", ""))
                self.tokenCombo.addItem(
                    f"{label} ({serial})" if label else serial,
                    userData=serial,
                )
            index = max(0, self.tokenCombo.findData(current))
            self.tokenCombo.setCurrentIndex(index)
        finally:
            self.tokenCombo.blockSignals(False)

    def _goto_tab(self, route: str) -> None:
        show_tab = getattr(self.window(), "show_tab", None)
        if callable(show_tab):
            show_tab(route)

    def _rebuild_recommendations(self, items: list[dict]) -> None:
        """Empfehlungs-Zeilen neu aufbauen (Text + Sprung-Button)."""
        for row in self._reco_rows:
            self.recoBox.removeWidget(row)
            row.deleteLater()
        self._reco_rows.clear()
        if not items:
            label = BodyLabel("Keine offenen Punkte — alles bereit.", self)
            label.setObjectName("recoEmptyLabel")
            self.recoBox.addWidget(label)
            self._reco_rows.append(label)
            return
        for entry in items:
            row = QWidget(self)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            text = BodyLabel(str(entry.get("text", "")), self)
            text.setWordWrap(True)
            row_layout.addWidget(text, 1)
            route = entry.get("route")
            if route:
                jump = PushButton("Öffnen", self)
                jump.setObjectName(f"recoJump_{route}")
                jump.clicked.connect(
                    lambda _checked=False, r=route: self._goto_tab(r)
                )
                row_layout.addWidget(jump)
            self.recoBox.addWidget(row)
            self._reco_rows.append(row)

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

    def _on_finished(self, result: dict[str, Any]) -> None:
        self.refreshButton.setEnabled(True)
        self._worker = None
        auto, self._auto = self._auto, False

        self._rebuild_recommendations(result.get("recommendations") or [])

        device = result["device"]
        if device is None:
            self.deviceLabel.setText("Nicht erkannt.")
        else:
            self.deviceLabel.setText(
                f"Erkannt: {device['label']} "
                f"(Modell: {device['model']}, "
                f"Seriennummer: {device['serial']})"
            )

        self._fill_token_combo(result.get("tokens") or [])

        bar_messages: list[tuple[str, str]] = []  # (Stufe, Text)

        pin_flags = result["pin_flags"]
        if pin_flags is None:
            self.pinFlagsTable.setRowCount(0)
        else:
            self.pinFlagsTable.setRowCount(len(PIN_FLAG_LABELS))
            for row, (key, label) in enumerate(PIN_FLAG_LABELS):
                value = "ja" if pin_flags.get(key) else "nein"
                self.pinFlagsTable.setItem(row, 0, QTableWidgetItem(label))
                self.pinFlagsTable.setItem(row, 1, QTableWidgetItem(value))
            critical = [key for key in pc.CRITICAL_PIN_FLAGS if pin_flags.get(key)]
            if critical:
                names = ", ".join(
                    label for key, label in PIN_FLAG_LABELS if key in critical
                )
                bar_messages.append((
                    "warning",
                    f"PIN-Status kritisch ({names}) — siehe PIN-Tab "
                    "(Entsperren/Ändern).",
                ))

        for message in result["errors"]:
            bar_messages.append(("error", message))

        bar_key = tuple(f"{level}:{text}" for level, text in bar_messages)
        if not auto or bar_key != self._last_bar_key:
            close_info_bars(self._info_bars)
            for level, text in bar_messages:
                if level == "warning":
                    self._show_warning(text)
                else:
                    self._show_error(text)
            self._last_bar_key = bar_key

    def _on_error(self, exc: Exception) -> None:
        """Sicherheitsnetz (eigentlich fängt _query_status alles ab)."""
        self.refreshButton.setEnabled(True)
        self._worker = None
        self._auto = False
        close_info_bars(self._info_bars)
        self._show_error(f"Unerwarteter Fehler ({exc}).")
