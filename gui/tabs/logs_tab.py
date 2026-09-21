"""logs_tab.py — Logs-Bereich (reines Audit-Log, modusfrei lesbar).

Einzige Anzeige-Stelle für das Firmware-Audit-Log
(`~/.pico_hsm/update_audit.jsonl`): Hash-Chain-Status, Tabelle
Zeit/Status/Details, Limit-Auswahl. Geräte-Infos wohnen im
Status-Tab (keine Duplikate).

Ein FunctionWorker pro Refresh liest nur die lokale Datei (kein
Hardware-Zugriff); Fehler erscheinen als inline InfoBar. Lesen geht
ohne Board — der Tab ist in jedem Modus aktiv.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QHBoxLayout, QTableWidgetItem, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CompactSpinBox,
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    StrongBodyLabel,
    TableWidget,
    TitleLabel,
)

from gui.session_helpers import close_info_bars
from gui.workers import FunctionWorker
from pico_hsm_tools import audit_log

AUDIT_COLUMNS = ("Zeit", "Status", "Details")
DEFAULT_AUDIT_LIMIT = 20


def _query_logs(limit: int) -> dict[str, Any]:
    """Audit-Log lesen (läuft im Worker-Thread, nur lokale Datei)."""
    errors: list[str] = []
    try:
        intact = audit_log.fc.verify_audit_chain()
        entries = audit_log.tail_flash_audit_log(limit)
    except Exception as exc:  # noqa: BLE001 — als InfoBar, kein Abbruch
        intact = False
        entries = []
        errors.append(f"Audit-Log nicht lesbar ({exc}).")
    return {"intact": intact, "entries": entries, "errors": errors}


class LogsTab(QWidget):
    """Logs-Tab: Audit-Log mit Hash-Chain-Status."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("logs")
        self._info_bars: list[InfoBar] = []
        self._worker: FunctionWorker | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(TitleLabel("Logs", self))

        # --- Audit ------------------------------------------------------
        audit_head = QHBoxLayout()
        audit_head.addWidget(StrongBodyLabel("Audit-Log", self))
        audit_head.addStretch(1)
        self.logsAuditLimitSpin = CompactSpinBox(self)
        self.logsAuditLimitSpin.setObjectName("logsAuditLimitSpin")
        self.logsAuditLimitSpin.setRange(1, 200)
        self.logsAuditLimitSpin.setValue(DEFAULT_AUDIT_LIMIT)
        self.logsRefreshButton = PrimaryPushButton("Aktualisieren", self)
        self.logsRefreshButton.setObjectName("logsRefreshButton")
        self.logsRefreshButton.clicked.connect(self.refresh)
        audit_head.addWidget(self.logsAuditLimitSpin)
        audit_head.addWidget(self.logsRefreshButton)
        layout.addLayout(audit_head)
        self.logsAuditChainLabel = BodyLabel("Noch nicht abgefragt.", self)
        self.logsAuditChainLabel.setObjectName("logsAuditChainLabel")
        layout.addWidget(self.logsAuditChainLabel)
        self.logsAuditTable = TableWidget(self)
        self.logsAuditTable.setObjectName("logsAuditTable")
        self.logsAuditTable.setColumnCount(len(AUDIT_COLUMNS))
        self.logsAuditTable.setHorizontalHeaderLabels(list(AUDIT_COLUMNS))
        self.logsAuditTable.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.logsAuditTable, 1)

        layout.addStretch(0)

    def apply_device_mode(self, mode: object, _state: object = None) -> None:
        """Logs sind rein lesend — in jedem Modus nutzbar, nichts sperren."""

    # --- Refresh ---------------------------------------------------------

    def refresh(self) -> None:
        """Audit-Log neu laden (Button + Tab-Wechsel)."""
        close_info_bars(self._info_bars)

        limit = self.logsAuditLimitSpin.value()

        self.logsRefreshButton.setEnabled(False)
        self._worker = FunctionWorker(_query_logs, limit)
        self._worker.signals.finished.connect(self._on_finished)
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

    def _on_finished(self, result: dict[str, Any]) -> None:
        self.logsRefreshButton.setEnabled(True)
        self._worker = None

        if result["intact"]:
            self.logsAuditChainLabel.setText("[OK] Hash-Chain intakt.")
        else:
            self.logsAuditChainLabel.setText(
                "[WARN] Hash-Chain GEBROCHEN — Log möglicherweise verändert."
            )
        entries = result["entries"]
        self.logsAuditTable.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            self.logsAuditTable.setItem(row, 0, QTableWidgetItem(entry.timestamp))
            self.logsAuditTable.setItem(row, 1, QTableWidgetItem(entry.status))
            self.logsAuditTable.setItem(
                row, 2, QTableWidgetItem(str(entry.details)),
            )
        if not entries:
            self._show_warning(
                "Kein Audit-Log vorhanden (noch kein Firmware-Update "
                "mit dieser App protokolliert)."
            )

        for message in result["errors"]:
            self._show_error(message)

    def _on_error(self, exc: Exception) -> None:
        """Sicherheitsnetz (eigentlich fängt _query_logs alles ab)."""
        self.logsRefreshButton.setEnabled(True)
        self._worker = None
        self._show_error(f"Unerwarteter Fehler ({exc}).")
