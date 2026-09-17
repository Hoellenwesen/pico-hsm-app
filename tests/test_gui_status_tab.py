"""
test_gui_status_tab.py — Status-Tab-Tests ohne Display und ohne Hardware.

Core (get_token, get_status_snapshot) und Config-Speicherung werden
gemockt; der FunctionWorker läuft echt im Thread-Pool (Muster für
Schritt 6). Getestet: Aufbau, Config-Laden/Speichern, alle drei
Sektionen, Fehler-InfoBars, Limit, Refresh-bei-Tab-Wechsel.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

from PySide6.QtCore import Qt
from qfluentwidgets import InfoBar

from gui import config as gui_config
from gui.main_window import MainWindow
from gui.tabs import status_tab as status_mod
from pico_hsm_tools import gateway_status
from pico_hsm_tools.pkcs11_session import GatewayState


def _entries():
    return [
        gateway_status.AuditEntry("2026-09-01T10:00:00", "flashed", {"v": "1"}),
        gateway_status.AuditEntry("2026-09-02T11:00:00", "flashed", {"v": "2"}),
    ]


def _snapshot(host="gw", port=8443, reachable=True, intact=True, entries=None):
    # Mirrors real semantics: no address -> never reachable.
    if not host or not port:
        reachable = False
    return gateway_status.StatusSnapshot(
        gateway=GatewayState(reachable=reachable, host=host, port=port),
        audit_chain_intact=intact,
        recent_flash_events=_entries() if entries is None else entries,
    )


def _install_mocks(monkeypatch, snapshot=None, token_error=None):
    """Core + Config-Speicherung mocken. Gibt calls-Dict zurück."""
    calls: dict = {"snapshots": [], "saves": 0}

    def fake_token():
        if token_error is not None:
            raise token_error
        return SimpleNamespace(label="MockToken", model="MockMod", serial="SN1")

    def fake_snapshot(host=None, port=None, audit_limit=20):
        calls["snapshots"].append((host, port, audit_limit))
        return snapshot if snapshot is not None else _snapshot(host, port)

    def fake_save(path=None):
        calls["saves"] += 1

    monkeypatch.setattr(status_mod, "get_token", fake_token)
    monkeypatch.setattr(
        gateway_status, "get_status_snapshot", fake_snapshot,
    )
    monkeypatch.setattr(gui_config, "save_config", fake_save)
    return calls


def _make_tab(qtbot, monkeypatch, **kwargs):
    calls = _install_mocks(monkeypatch, **kwargs)
    monkeypatch.setattr(gui_config.cfg.gatewayHost, "value", "")
    monkeypatch.setattr(gui_config.cfg.gatewayPort, "value", 0)
    tab = status_mod.StatusTab()
    qtbot.addWidget(tab)
    tab.show()
    return tab, calls


# --- Aufbau --------------------------------------------------------------------

def test_builds_with_all_sections(qtbot, monkeypatch):
    tab, _ = _make_tab(qtbot, monkeypatch)
    for name in (
        "deviceLabel", "gatewayHostEdit", "gatewayPortSpin", "refreshButton",
        "gatewayResultLabel", "auditChainLabel", "auditTable", "auditLimitSpin",
    ):
        assert tab.findChild(object, name) is not None, name
    assert tab.auditTable.columnCount() == 3


def test_fields_load_from_config(qtbot, monkeypatch):
    _install_mocks(monkeypatch)
    monkeypatch.setattr(gui_config.cfg.gatewayHost, "value", "gw.test")
    monkeypatch.setattr(gui_config.cfg.gatewayPort, "value", 8443)
    tab = status_mod.StatusTab()
    qtbot.addWidget(tab)
    try:
        assert tab.gatewayHostEdit.text() == "gw.test"
        assert tab.gatewayPortSpin.value() == 8443
    finally:
        tab.close()


# --- Refresh ---------------------------------------------------------------------

def test_refresh_fills_all_sections(qtbot, monkeypatch):
    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        tab.gatewayHostEdit.setText("gw.test")
        tab.gatewayPortSpin.setValue(8443)
        qtbot.mouseClick(tab.refreshButton, Qt.LeftButton)
        qtbot.waitUntil(
            lambda: tab.deviceLabel.text() != "Noch nicht abgefragt.",
            timeout=5000,
        )
        assert "MockToken" in tab.deviceLabel.text()
        assert "erreichbar" in tab.gatewayResultLabel.text()
        assert "intakt" in tab.auditChainLabel.text()
        assert tab.auditTable.rowCount() == 2
        assert calls["saves"] == 1
        assert calls["snapshots"] == [("gw.test", 8443, 20)]
    finally:
        tab.close()


def test_refresh_without_gateway_address(qtbot, monkeypatch):
    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        tab.refresh()
        qtbot.waitUntil(
            lambda: tab.deviceLabel.text() != "Noch nicht abgefragt.",
            timeout=5000,
        )
        assert calls["snapshots"] == [(None, None, 20)]
        assert "Gateway-Adresse konfiguriert" in tab.gatewayResultLabel.text()
    finally:
        tab.close()


def test_refresh_limit_controls_snapshot(qtbot, monkeypatch):
    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        tab.auditLimitSpin.setValue(5)
        tab.refresh()
        qtbot.waitUntil(
            lambda: tab.deviceLabel.text() != "Noch nicht abgefragt.",
            timeout=5000,
        )
        assert calls["snapshots"][-1][2] == 5
    finally:
        tab.close()


def test_device_failure_blocks_nothing(qtbot, monkeypatch):
    tab, _ = _make_tab(
        qtbot, monkeypatch, token_error=RuntimeError("kein Board"),
    )
    try:
        tab.refresh()
        qtbot.waitUntil(
            lambda: bool(tab.findChildren(InfoBar)),
            timeout=5000,
        )
        assert tab.deviceLabel.text() == "Nicht erkannt."
        assert "Gateway-Adresse konfiguriert" in tab.gatewayResultLabel.text()
        assert tab.auditTable.rowCount() == 2
    finally:
        tab.close()


def test_broken_chain_and_empty_log(qtbot, monkeypatch):
    tab, _ = _make_tab(
        qtbot, monkeypatch,
        snapshot=_snapshot(intact=False, entries=[]),
    )
    try:
        tab.refresh()
        qtbot.waitUntil(
            lambda: "GEBROCHEN" in tab.auditChainLabel.text(),
            timeout=5000,
        )
        assert tab.auditTable.rowCount() == 0
        qtbot.waitUntil(
            lambda: bool(tab.findChildren(InfoBar)),
            timeout=5000,
        )
    finally:
        tab.close()


def test_refresh_button_disabled_during_run(qtbot, monkeypatch):
    tab, _ = _make_tab(qtbot, monkeypatch)
    try:
        tab.refresh()
        assert tab.refreshButton.isEnabled() is False
        qtbot.waitUntil(tab.refreshButton.isEnabled, timeout=5000)
    finally:
        tab.close()


# --- Tab-Wechsel -------------------------------------------------------------------

def test_switch_to_status_triggers_refresh(qtbot, monkeypatch):
    """MainWindow-Muster: Wechsel auf den Tab ruft refresh().

    Umweg über "keys" (nicht "setup"): keys.refresh() ohne PIN bricht
    sauber ab, während setup.refresh() einen echten nativen
    PC/SC-Call auslösen würde (F7) — keys ist der sichere
    Zwischen-Tab für alle Switch-Tests.
    """
    calls = _install_mocks(monkeypatch)
    monkeypatch.setattr(gui_config.cfg.gatewayHost, "value", "")
    monkeypatch.setattr(gui_config.cfg.gatewayPort, "value", 0)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    try:
        status_tab = window.tab("status")
        assert status_tab.deviceLabel.text() == "Noch nicht abgefragt."
        window.navigationInterface.widget("keys").clicked.emit(True)
        qtbot.waitUntil(
            lambda: window.stackedWidget.currentWidget() is window.tab("keys"),
            timeout=3000,
        )
        window.navigationInterface.widget("status").clicked.emit(True)
        qtbot.waitUntil(
            lambda: status_tab.deviceLabel.text() != "Noch nicht abgefragt.",
            timeout=5000,
        )
        assert calls["saves"] >= 1
    finally:
        window.close()
