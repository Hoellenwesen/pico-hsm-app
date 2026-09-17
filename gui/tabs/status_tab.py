"""status_tab.py — Status-Bereich (Schritt 6a: erster ausgebauter Tab, Muster).

Drei Sektionen wie CLI `status device/gateway/audit`: Gerät (read-only,
kein PKCS#11-Lock), Gateway-Erreichbarkeit (Host/Port aus Config),
Audit-Log-Tabelle. Genau EIN FunctionWorker pro Refresh sammelt alles;
Fehler pro Sektion blockieren einander nicht und erscheinen als inline
InfoBar (kein Dialog bei Routine-Abfragen — getroffene Entscheidung).

Tab-Wechsel-Refresh läuft über MainWindow (currentChanged -> refresh(),
Duck-Typing-Muster für alle Schritt-6-Tabs).
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
    LineEdit,
    PrimaryPushButton,
    StrongBodyLabel,
    TableWidget,
    TitleLabel,
)

from gui import config as gui_config
from gui.workers import FunctionWorker
from pico_hsm_tools import gateway_status
from pico_hsm_tools.pkcs11_session import get_token

AUDIT_COLUMNS = ("Zeit", "Status", "Details")
DEFAULT_AUDIT_LIMIT = 20


def _query_status(
    host: str | None, port: int | None, limit: int,
) -> dict[str, Any]:
    """Alle Sektionen einsammeln (läuft im Worker-Thread).

    Gibt dict mit device-Dict/None, gateway-Dict, audit-Teilen und einer
    Fehlerliste zurück — ein fehlender Board blockiert z.B. weder Gateway
    noch Audit-Anzeige.
    """
    errors: list[str] = []
    try:
        token = get_token()
        device: dict[str, Any] | None = {
            "label": token.label,
            "model": token.model,
            "serial": token.serial,
        }
    except Exception as exc:  # noqa: BLE001 — als InfoBar, kein Abbruch
        device = None
        errors.append(f"Kein Gerät erkannt ({exc}).")

    try:
        snapshot = gateway_status.get_status_snapshot(host, port, limit)
        gateway_error: str | None = None
    except Exception as exc:  # noqa: BLE001 — Sicherheitsnetz
        snapshot = None  # type: ignore[assignment]
        gateway_error = f"Statusabfrage fehlgeschlagen ({exc})."
        errors.append(gateway_error)

    return {
        "device": device,
        "snapshot": snapshot,
        "errors": errors,
    }


class StatusTab(QWidget):
    """Status-Tab: Gerät, Gateway, Audit-Log."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("status")
        self._info_bars: list[InfoBar] = []
        self._worker: FunctionWorker | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(TitleLabel("Status", self))

        # --- Gerät ------------------------------------------------------
        layout.addWidget(StrongBodyLabel("Gerät", self))
        self.deviceLabel = BodyLabel("Noch nicht abgefragt.", self)
        self.deviceLabel.setObjectName("deviceLabel")
        layout.addWidget(self.deviceLabel)

        # --- Gateway ----------------------------------------------------
        layout.addWidget(StrongBodyLabel("Gateway", self))
        gateway_row = QHBoxLayout()
        self.gatewayHostEdit = LineEdit(self)
        self.gatewayHostEdit.setObjectName("gatewayHostEdit")
        self.gatewayHostEdit.setPlaceholderText("Hostname/IP (leer = nicht konfiguriert)")
        self.gatewayHostEdit.setText(gui_config.cfg.gatewayHost.value)
        self.gatewayPortSpin = CompactSpinBox(self)
        self.gatewayPortSpin.setObjectName("gatewayPortSpin")
        self.gatewayPortSpin.setRange(0, 65535)
        self.gatewayPortSpin.setValue(gui_config.cfg.gatewayPort.value)
        self.refreshButton = PrimaryPushButton("Aktualisieren", self)
        self.refreshButton.setObjectName("refreshButton")
        self.refreshButton.clicked.connect(self.refresh)
        gateway_row.addWidget(self.gatewayHostEdit, 1)
        gateway_row.addWidget(self.gatewayPortSpin)
        gateway_row.addWidget(self.refreshButton)
        layout.addLayout(gateway_row)
        self.gatewayResultLabel = BodyLabel("Noch nicht abgefragt.", self)
        self.gatewayResultLabel.setObjectName("gatewayResultLabel")
        layout.addWidget(self.gatewayResultLabel)

        # --- Audit ------------------------------------------------------
        audit_head = QHBoxLayout()
        audit_head.addWidget(StrongBodyLabel("Audit-Log", self))
        audit_head.addStretch(1)
        self.auditLimitSpin = CompactSpinBox(self)
        self.auditLimitSpin.setObjectName("auditLimitSpin")
        self.auditLimitSpin.setRange(1, 200)
        self.auditLimitSpin.setValue(DEFAULT_AUDIT_LIMIT)
        audit_head.addWidget(self.auditLimitSpin)
        layout.addLayout(audit_head)
        self.auditChainLabel = BodyLabel("Noch nicht abgefragt.", self)
        self.auditChainLabel.setObjectName("auditChainLabel")
        layout.addWidget(self.auditChainLabel)
        self.auditTable = TableWidget(self)
        self.auditTable.setObjectName("auditTable")
        self.auditTable.setColumnCount(len(AUDIT_COLUMNS))
        self.auditTable.setHorizontalHeaderLabels(list(AUDIT_COLUMNS))
        self.auditTable.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.auditTable, 1)

        layout.addStretch(0)

    # --- Refresh ---------------------------------------------------------

    def refresh(self) -> None:
        """Alle Sektionen neu abfragen (Button + Tab-Wechsel)."""
        for bar in self._info_bars:
            bar.close()
        self._info_bars.clear()

        host = self.gatewayHostEdit.text().strip() or None
        port_value = self.gatewayPortSpin.value()
        port = port_value if port_value > 0 else None
        limit = self.auditLimitSpin.value()

        gui_config.cfg.gatewayHost.value = self.gatewayHostEdit.text().strip()
        gui_config.cfg.gatewayPort.value = port_value
        gui_config.save_config()

        self.refreshButton.setEnabled(False)
        self._worker = FunctionWorker(_query_status, host, port, limit)
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
        self.refreshButton.setEnabled(True)
        self._worker = None

        device = result["device"]
        if device is None:
            self.deviceLabel.setText("Nicht erkannt.")
        else:
            self.deviceLabel.setText(
                f"Erkannt: {device['label']} "
                f"(Modell: {device['model']}, "
                f"Seriennummer: {device['serial']})"
            )

        snapshot = result["snapshot"]
        if snapshot is not None:
            gateway = snapshot.gateway
            if gateway.reachable:
                self.gatewayResultLabel.setText(
                    f"[OK] Gateway unter {gateway.host}:{gateway.port} erreichbar."
                )
            elif gateway.host:
                self.gatewayResultLabel.setText(
                    f"[INFO] Gateway unter {gateway.host}:{gateway.port} "
                    "nicht erreichbar."
                )
            else:
                self.gatewayResultLabel.setText(
                    "[INFO] Keine Gateway-Adresse konfiguriert."
                )
            if snapshot.audit_chain_intact:
                self.auditChainLabel.setText("[OK] Hash-Chain intakt.")
            else:
                self.auditChainLabel.setText(
                    "[WARN] Hash-Chain GEBROCHEN — Log möglicherweise verändert."
                )
            entries = snapshot.recent_flash_events
            self.auditTable.setRowCount(len(entries))
            for row, entry in enumerate(entries):
                self.auditTable.setItem(row, 0, QTableWidgetItem(entry.timestamp))
                self.auditTable.setItem(row, 1, QTableWidgetItem(entry.status))
                self.auditTable.setItem(
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
        """Sicherheitsnetz (eigentlich fängt _query_status alles ab)."""
        self.refreshButton.setEnabled(True)
        self._worker = None
        self._show_error(f"Unerwarteter Fehler ({exc}).")
