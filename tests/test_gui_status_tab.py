"""
test_gui_status_tab.py — Status-Tab-Tests ohne Display und ohne Hardware.

Core (get_token, list_tokens, read_pin_flags, audit_chain_intact) und
Config-Speicherung werden gemockt; der FunctionWorker läuft echt im
Thread-Pool. Getestet: Aufbau, Token-Auswahl, beide Sektionen (Gerät,
PIN-Status), Empfehlungen, Fehler-InfoBars, Auto-Refresh,
Refresh-bei-Tab-Wechsel.
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
from pico_hsm_tools import audit_log


def _install_mocks(monkeypatch, chain_intact=True, token_error=None,
                   pin_flags=None, pin_error=None, tokens=None,
                   setup_done=None, backup_infos=None):
    """Core + Config-Speicherung mocken. Gibt calls-Dict zurück."""
    calls: dict = {"saves": 0, "serials": [], "chains": 0}

    def fake_token(*args, **kwargs):
        calls["serials"].append(kwargs.get("serial"))
        if token_error is not None:
            raise token_error
        return SimpleNamespace(label="MockToken", model="MockMod", serial="SN1")

    def fake_pin_flags(lib_path=None, serial=None):
        if pin_error is not None:
            raise pin_error
        if pin_flags is not None:
            return pin_flags
        return {
            "login_required": True,
            "user_pin_initialized": True,
            "user_pin_count_low": False,
            "user_pin_final_try": False,
            "user_pin_locked": False,
            "user_pin_to_be_changed": False,
        }

    def fake_chain():
        calls["chains"] += 1
        return chain_intact

    def fake_save(path=None):
        calls["saves"] += 1

    monkeypatch.setattr(status_mod, "get_token", fake_token)
    monkeypatch.setattr(
        status_mod.backup_index, "list_backups",
        lambda parent_dir: (
            backup_infos if backup_infos is not None else []
        ),
    )
    from gui.tabs import wizard_tab as wiz_mod

    monkeypatch.setattr(
        wiz_mod, "load_state",
        lambda serial=None: (
            setup_done if setup_done is not None
            else {k: True for k in wiz_mod.STEP_KEYS}
        ),
    )
    monkeypatch.setattr(status_mod, "list_tokens", lambda *a, **k: (
        tokens if tokens is not None else [
            {"label": "MockToken", "model": "MockMod", "serial": "SN1"},
            {"label": "ZweitToken", "model": "MockMod", "serial": "SN2"},
        ]
    ))
    monkeypatch.setattr(status_mod.pc, "read_pin_flags", fake_pin_flags)
    monkeypatch.setattr(audit_log, "audit_chain_intact", fake_chain)
    monkeypatch.setattr(gui_config, "save_config", fake_save)
    return calls


def _make_tab(qtbot, monkeypatch, **kwargs):
    calls = _install_mocks(monkeypatch, **kwargs)
    monkeypatch.setattr(gui_config.cfg.tokenSerial, "value", "")
    tab = status_mod.StatusTab()
    qtbot.addWidget(tab)
    tab.show()
    return tab, calls


# --- Aufbau --------------------------------------------------------------------

def test_builds_with_all_sections(qtbot, monkeypatch):
    tab, _ = _make_tab(qtbot, monkeypatch)
    for name in (
        "deviceLabel", "tokenCombo", "pinFlagsTable", "refreshButton",
    ):
        assert tab.findChild(object, name) is not None, name
    assert tab.findChild(object, "gatewayHostEdit") is None
    assert tab.pinFlagsTable.columnCount() == 2
    # Trennlinien zwischen den drei Sektionen:
    from qfluentwidgets import HorizontalSeparator
    assert len(tab.findChildren(HorizontalSeparator)) == 2


def test_token_selection_loads_from_config(qtbot, monkeypatch):
    _install_mocks(monkeypatch)
    monkeypatch.setattr(gui_config.cfg.tokenSerial, "value", "SN2")
    tab = status_mod.StatusTab()
    qtbot.addWidget(tab)
    try:
        assert tab.tokenCombo.currentData() == "SN2"
    finally:
        tab.close()


# --- Refresh ---------------------------------------------------------------------

def test_refresh_fills_all_sections(qtbot, monkeypatch):
    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        qtbot.mouseClick(tab.refreshButton, Qt.LeftButton)
        qtbot.waitUntil(
            lambda: tab.deviceLabel.text() != "Noch nicht abgefragt.",
            timeout=5000,
        )
        assert "MockToken" in tab.deviceLabel.text()
        assert calls["saves"] == 1
        assert calls["chains"] == 1
        assert tab.pinFlagsTable.rowCount() == 6
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
    finally:
        tab.close()


def test_token_selection_passes_serial(qtbot, monkeypatch):
    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        tab.refresh()
        qtbot.waitUntil(
            lambda: tab.deviceLabel.text() != "Noch nicht abgefragt.",
            timeout=5000,
        )
        assert tab.tokenCombo.count() == 3  # Automatisch + 2 Tokens
        assert calls["serials"] == [None]
        tab.tokenCombo.setCurrentIndex(2)
        tab.refresh()
        qtbot.waitUntil(lambda: calls["serials"] == [None, "SN2"], timeout=5000)
    finally:
        tab.close()


def test_pin_flags_critical_warns(qtbot, monkeypatch):
    tab, _ = _make_tab(
        qtbot, monkeypatch,
        pin_flags={
            "login_required": True,
            "user_pin_initialized": True,
            "user_pin_count_low": False,
            "user_pin_final_try": False,
            "user_pin_locked": True,
            "user_pin_to_be_changed": False,
        },
    )
    try:
        tab.refresh()
        qtbot.waitUntil(
            lambda: tab.pinFlagsTable.rowCount() == 6,
            timeout=5000,
        )
        qtbot.waitUntil(
            lambda: bool(tab.findChildren(InfoBar)),
            timeout=5000,
        )
        texts = "\n".join(
            bar.titleLabel.text() + " " + bar.contentLabel.text()
            for bar in tab.findChildren(InfoBar)
        )
        assert "gesperrt" in texts
    finally:
        tab.close()


def test_pin_flags_failure_blocks_nothing(qtbot, monkeypatch):
    tab, _ = _make_tab(
        qtbot, monkeypatch, pin_error=RuntimeError("kein Board"),
    )
    try:
        tab.refresh()
        qtbot.waitUntil(
            lambda: tab.deviceLabel.text() != "Noch nicht abgefragt.",
            timeout=5000,
        )
        assert tab.pinFlagsTable.rowCount() == 0
        assert "MockToken" in tab.deviceLabel.text()
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


# --- Auto-Refresh (60s, nur sichtbar, kein Doppel-Lauf, kein Blinken) ---------

def test_auto_timer_runs_only_when_visible(qtbot, monkeypatch):
    tab, _ = _make_tab(qtbot, monkeypatch)
    try:
        assert tab._auto_timer.interval() == 60_000
        assert tab._auto_timer.isActive()
        tab.hide()
        assert not tab._auto_timer.isActive()
        tab.show()
        assert tab._auto_timer.isActive()
    finally:
        tab.close()


def test_refresh_ignores_second_call_while_busy(qtbot, monkeypatch):
    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        tab._worker = object()  # simulierter laufender Worker
        tab.refresh()
        tab.refresh(auto=True)
        qtbot.wait(300)
        assert calls["serials"] == []
    finally:
        tab._worker = None
        tab.close()


def test_auto_refresh_dedupes_unchanged_errors(qtbot, monkeypatch):
    tab, _ = _make_tab(
        qtbot, monkeypatch, token_error=RuntimeError("kein Board"),
    )
    try:
        tab.refresh(auto=True)
        qtbot.waitUntil(
            lambda: bool(tab.findChildren(InfoBar)),
            timeout=5000,
        )
        assert len(tab.findChildren(InfoBar)) == 1
        tab.refresh(auto=True)
        qtbot.waitUntil(tab.refreshButton.isEnabled, timeout=5000)
        qtbot.wait(300)
        # Unveränderte Lage: Bars bleiben stehen, keine Duplikate.
        assert len(tab.findChildren(InfoBar)) == 1
    finally:
        tab.close()


# --- Empfehlungen (Dashboard) ---------------------------------------------------------

def test_recommendations_empty_when_all_good(qtbot, monkeypatch):
    tab, _ = _make_tab(qtbot, monkeypatch)
    try:
        tab.refresh()
        qtbot.waitUntil(
            lambda: tab.deviceLabel.text() != "Noch nicht abgefragt.",
            timeout=5000,
        )
        qtbot.waitUntil(
            lambda: tab.findChild(object, "recoEmptyLabel") is not None,
            timeout=5000,
        )
    finally:
        tab.close()


def test_recommendations_jump_to_pin(qtbot, monkeypatch):
    tab, _ = _make_tab(
        qtbot, monkeypatch,
        pin_flags={
            "login_required": True,
            "user_pin_initialized": True,
            "user_pin_count_low": False,
            "user_pin_final_try": False,
            "user_pin_locked": True,
            "user_pin_to_be_changed": False,
        },
    )
    try:
        tab.refresh()
        qtbot.waitUntil(
            lambda: tab.findChild(object, "recoJump_pin") is not None,
            timeout=5000,
        )
        jumps: list = []
        monkeypatch.setattr(
            tab, "window", lambda: SimpleNamespace(show_tab=jumps.append),
        )
        qtbot.mouseClick(tab.findChild(object, "recoJump_pin"), Qt.LeftButton)
        assert jumps == ["pin"]
    finally:
        tab.close()


def test_recommendations_include_setup_and_backup(qtbot, monkeypatch):
    from pico_hsm_tools import backup_index as bi_mod

    infos = [
        SimpleNamespace(
            problems=["Ciphertext-Datei fehlt"],
            leftover_shares_file_present=False,
            drill_state="nie", drill_age_days=None, age_days=5,
            last_drill_at=None, last_drill_result=None,
        ),
    ]
    tab, _ = _make_tab(
        qtbot, monkeypatch, setup_done={"detect": True, "init": False},
        backup_infos=infos,
    )
    try:
        tab.refresh()
        qtbot.waitUntil(
            lambda: tab.findChild(object, "recoJump_wizard") is not None,
            timeout=5000,
        )
        assert tab.findChild(object, "recoJump_backup") is not None
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
    monkeypatch.setattr(gui_config.cfg.tokenSerial, "value", "")
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
