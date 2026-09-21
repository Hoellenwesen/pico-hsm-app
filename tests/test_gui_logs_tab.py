"""
test_gui_logs_tab.py — Logs-Tab-Tests ohne Display und ohne Hardware.

Audit-Core (verify_audit_chain, tail_flash_audit_log) wird gemockt;
der FunctionWorker läuft echt im Thread-Pool. Getestet: Aufbau,
Chain-Status, Tabelle, Limit, leeres Log, Modusfreiheit,
Refresh-bei-Tab-Wechsel. Reines Audit-Log — Gerät wohnt
im Status-Tab.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from qfluentwidgets import InfoBar

from gui.main_window import MainWindow
from gui.tabs import logs_tab as logs_mod
from pico_hsm_tools import audit_log
from pico_hsm_tools.device_mode import DeviceMode


def _entries():
    return [
        audit_log.AuditEntry("2026-09-01T10:00:00", "flashed", {"v": "1"}),
        audit_log.AuditEntry("2026-09-02T11:00:00", "flashed", {"v": "2"}),
    ]


def _install_mocks(monkeypatch, intact=True, entries=None):
    calls: dict = {"tails": []}

    def fake_tail(limit=20):
        calls["tails"].append(limit)
        return _entries() if entries is None else entries

    monkeypatch.setattr(
        audit_log.fc, "verify_audit_chain", lambda: intact,
    )
    monkeypatch.setattr(audit_log, "tail_flash_audit_log", fake_tail)
    return calls


def _make_tab(qtbot, monkeypatch, **kwargs):
    calls = _install_mocks(monkeypatch, **kwargs)
    tab = logs_mod.LogsTab()
    qtbot.addWidget(tab)
    tab.show()
    return tab, calls


# --- Aufbau --------------------------------------------------------------------

def test_builds_with_audit_widgets(qtbot, monkeypatch):
    tab, _ = _make_tab(qtbot, monkeypatch)
    try:
        for name in (
            "logsRefreshButton", "logsAuditLimitSpin",
            "logsAuditChainLabel", "logsAuditTable",
        ):
            assert tab.findChild(object, name) is not None, name
        assert tab.logsAuditTable.columnCount() == 3
        # Keine Geräte-Duplikate (wohnen im Status-Tab):
        assert tab.findChild(object, "logsDeviceLabel") is None
    finally:
        tab.close()


def test_mode_gating_never_disables(qapp):
    """Logs sind rein lesend — apply_device_mode sperrt nichts."""
    tab = logs_mod.LogsTab()
    try:
        for mode in (
            DeviceMode.KEIN_GERAET, DeviceMode.BOOTSEL, DeviceMode.NORMAL,
        ):
            tab.apply_device_mode(mode)
            assert tab.logsRefreshButton.isEnabled()
            assert tab.logsAuditTable.isEnabled()
    finally:
        tab.close()


# --- Refresh ---------------------------------------------------------------------

def test_refresh_fills_chain_and_table(qtbot, monkeypatch):
    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        qtbot.mouseClick(tab.logsRefreshButton, Qt.LeftButton)
        qtbot.waitUntil(
            lambda: tab.logsAuditTable.rowCount() == 2,
            timeout=5000,
        )
        assert "intakt" in tab.logsAuditChainLabel.text()
        assert calls["tails"] == [20]
    finally:
        tab.close()


def test_refresh_limit_controls_tail(qtbot, monkeypatch):
    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        tab.logsAuditLimitSpin.setValue(5)
        tab.refresh()
        qtbot.waitUntil(
            lambda: tab.logsAuditTable.rowCount() == 2,
            timeout=5000,
        )
        assert calls["tails"] == [5]
    finally:
        tab.close()


def test_broken_chain_and_empty_log(qtbot, monkeypatch):
    tab, _ = _make_tab(qtbot, monkeypatch, intact=False, entries=[])
    try:
        tab.refresh()
        qtbot.waitUntil(
            lambda: "GEBROCHEN" in tab.logsAuditChainLabel.text(),
            timeout=5000,
        )
        assert tab.logsAuditTable.rowCount() == 0
        qtbot.waitUntil(
            lambda: bool(tab.findChildren(InfoBar)),
            timeout=5000,
        )
    finally:
        tab.close()


# --- Tab-Wechsel -------------------------------------------------------------------

def test_switch_to_logs_triggers_refresh(qtbot, monkeypatch):
    _install_mocks(monkeypatch)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    try:
        logs_tab = window.tab("logs")
        assert logs_tab.logsAuditTable.rowCount() == 0
        window.navigationInterface.widget("keys").clicked.emit(True)
        qtbot.waitUntil(
            lambda: window.stackedWidget.currentWidget() is window.tab("keys"),
            timeout=3000,
        )
        window.navigationInterface.widget("logs").clicked.emit(True)
        qtbot.waitUntil(
            lambda: logs_tab.logsAuditTable.rowCount() == 2,
            timeout=5000,
        )
    finally:
        window.close()
