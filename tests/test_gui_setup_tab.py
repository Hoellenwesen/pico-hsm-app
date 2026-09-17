"""
test_gui_setup_tab.py — Setup-Tab-Tests ohne Display und ohne Hardware.

Core (flash_core, apdu_core) wird gemockt, Worker laufen echt. Deckt ab:
Aufbau, OTP-Tabelle, Datetime-Anzeige/Setzen (inkl. Confirm-Abbruch),
Dynamic-Options-Anwenden (inkl. P2C-Deaktivierungs-Warntext),
Fehler-InfoBars, Disconnect-Hygiene, Refresh-bei-Tab-Wechsel.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import datetime

from PySide6.QtCore import QDateTime, Qt
from qfluentwidgets import InfoBar

from gui.main_window import MainWindow
from gui.tabs import setup_tab as setup_mod
from pico_hsm_tools import apdu_core as ac
from pico_hsm_tools import flash_core as fc


class FakeConnection:
    def __init__(self):
        self.disconnects = 0

    def disconnect(self):
        self.disconnects += 1


def _install_mocks(monkeypatch, fingerprint="AA" * 32, dt=None,
                   ptc=True, counter=False, otp_error=None, apdu_error=None):
    """Core mocken. Gibt (calls, connection) zurück."""
    calls: dict = {"sets": [], "options": [], "confirms": []}
    connection = FakeConnection()
    moment = dt or datetime(2022, 4, 6, 19, 41, 23)

    def fake_fingerprint():
        if otp_error is not None:
            raise otp_error
        return fingerprint

    def fake_open():
        if apdu_error is not None:
            raise apdu_error
        return connection

    def fake_set_datetime(conn, value):
        calls["sets"].append(value)

    def fake_set_options(conn, options):
        calls["options"].append(options)

    monkeypatch.setattr(fc, "get_burned_key_fingerprint", fake_fingerprint)
    monkeypatch.setattr(fc, "read_otp_field", lambda field: f"mock-{field}")
    monkeypatch.setattr(ac, "open_connection", fake_open)
    monkeypatch.setattr(ac, "get_datetime", lambda conn: moment)
    monkeypatch.setattr(
        ac, "get_dynamic_options",
        lambda conn: ac.DynamicOptions(
            press_to_confirm=ptc, key_usage_counter=counter,
        ),
    )
    monkeypatch.setattr(ac, "set_datetime", fake_set_datetime)
    monkeypatch.setattr(ac, "set_dynamic_options", fake_set_options)
    monkeypatch.setattr(
        setup_mod, "confirm_destructive",
        lambda parent, title, text: calls["confirms"].append((title, text)) or True,
    )
    return calls, connection


def _make_tab(qtbot, monkeypatch, **kwargs):
    mocks = _install_mocks(monkeypatch, **kwargs)
    tab = setup_mod.SetupTab()
    qtbot.addWidget(tab)
    tab.show()
    return tab, mocks


# --- Aufbau --------------------------------------------------------------------

def test_builds_with_all_sections(qtbot, monkeypatch):
    tab, _ = _make_tab(qtbot, monkeypatch)
    try:
        for name in (
            "fingerprintLabel", "otpTable", "datetimeCurrentLabel",
            "datetimeEdit", "nowButton", "datetimeSetButton",
            "ptcSwitch", "counterSwitch", "dynoptsApplyButton",
            "setupRefreshButton",
        ):
            assert tab.findChild(object, name) is not None, name
    finally:
        tab.close()


# --- Laden -----------------------------------------------------------------------

def test_refresh_fills_all_sections(qtbot, monkeypatch):
    tab, _ = _make_tab(qtbot, monkeypatch)
    try:
        tab.refresh()
        qtbot.waitUntil(
            lambda: tab.otpTable.rowCount() == len(fc.OTP_FLAG_FIELDS),
            timeout=5000,
        )
        assert "AA" in tab.fingerprintLabel.text()
        assert "2022-04-06 19:41:23" in tab.datetimeCurrentLabel.text()
        assert tab.ptcSwitch.isChecked() is True
        assert tab.counterSwitch.isChecked() is False
        assert tab.findChildren(InfoBar) == []
    finally:
        tab.close()


def test_fingerprint_failure_blocks_nothing(qtbot, monkeypatch):
    tab, _ = _make_tab(
        qtbot, monkeypatch, otp_error=RuntimeError("kein Board"),
    )
    try:
        tab.refresh()
        qtbot.waitUntil(lambda: bool(tab.findChildren(InfoBar)), timeout=5000)
        assert "nicht lesbar" in tab.fingerprintLabel.text()
        assert tab.otpTable.rowCount() == len(fc.OTP_FLAG_FIELDS)
        assert "19:41:23" in tab.datetimeCurrentLabel.text()
    finally:
        tab.close()


def test_apdu_failure_blocks_nothing(qtbot, monkeypatch):
    tab, _ = _make_tab(
        qtbot, monkeypatch, apdu_error=RuntimeError("kein Reader"),
    )
    try:
        tab.refresh()
        qtbot.waitUntil(lambda: bool(tab.findChildren(InfoBar)), timeout=5000)
        assert "nicht lesbar" in tab.datetimeCurrentLabel.text()
        assert "AA" in tab.fingerprintLabel.text()
    finally:
        tab.close()


# --- Schreiben ---------------------------------------------------------------------

def test_datetime_set_encodes_and_disconnects(qtbot, monkeypatch):
    tab, (calls, connection) = _make_tab(qtbot, monkeypatch)
    try:
        tab.datetimeEdit.setDateTime(QDateTime(2022, 4, 6, 19, 41, 23))
        qtbot.mouseClick(tab.datetimeSetButton, Qt.LeftButton)
        qtbot.waitUntil(
            lambda: "2022-04-06 19:41:23" in tab.datetimeCurrentLabel.text(),
            timeout=5000,
        )
        assert calls["sets"] == [datetime(2022, 4, 6, 19, 41, 23)]
        assert connection.disconnects >= 1
        assert len(calls["confirms"]) == 1
    finally:
        tab.close()


def test_datetime_set_cancel_aborts(qtbot, monkeypatch):
    tab, (calls, _) = _make_tab(qtbot, monkeypatch)
    monkeypatch.setattr(
        setup_mod, "confirm_destructive", lambda *a: False,
    )
    try:
        qtbot.mouseClick(tab.datetimeSetButton, Qt.LeftButton)
        qtbot.wait(300)
        assert calls["sets"] == []
        assert "Noch nicht abgefragt" in tab.datetimeCurrentLabel.text()
    finally:
        tab.close()


def test_now_button_fills_current_time(qtbot, monkeypatch):
    tab, _ = _make_tab(qtbot, monkeypatch)
    try:
        tab.datetimeEdit.setDateTime(QDateTime(2001, 1, 1, 0, 0, 0))
        qtbot.mouseClick(tab.nowButton, Qt.LeftButton)
        assert tab.datetimeEdit.dateTime().toPython().date() == datetime.now().date()
    finally:
        tab.close()


def test_dynopts_apply_sends_mask(qtbot, monkeypatch):
    tab, (calls, connection) = _make_tab(qtbot, monkeypatch)
    try:
        tab.ptcSwitch.setChecked(False)
        tab.counterSwitch.setChecked(True)
        qtbot.mouseClick(tab.dynoptsApplyButton, Qt.LeftButton)
        qtbot.waitUntil(lambda: len(calls["options"]) == 1, timeout=5000)
        options = calls["options"][0]
        assert options.press_to_confirm is False
        assert options.key_usage_counter is True
        assert connection.disconnects >= 1
    finally:
        tab.close()


def test_ptc_disable_uses_hardened_text(qtbot, monkeypatch):
    tab, (calls, _) = _make_tab(qtbot, monkeypatch)
    try:
        tab._current_ptc = True
        tab.ptcSwitch.setChecked(False)
        tab._on_apply_options()
        qtbot.waitUntil(lambda: len(calls["options"]) == 1, timeout=5000)
        title, _ = calls["confirms"][-1]
        assert "DEAKTIVIEREN" in title
    finally:
        tab.close()


def test_dynopts_apply_without_changes_uses_plain_text(qtbot, monkeypatch):
    tab, (calls, _) = _make_tab(qtbot, monkeypatch)
    try:
        tab._current_ptc = True
        tab.ptcSwitch.setChecked(True)
        tab._on_apply_options()
        qtbot.waitUntil(lambda: len(calls["options"]) == 1, timeout=5000)
        title, _ = calls["confirms"][-1]
        assert "DEAKTIVIEREN" not in title
    finally:
        tab.close()


# --- Tab-Wechsel ---------------------------------------------------------------------

def test_switch_to_setup_triggers_refresh(qtbot, monkeypatch):
    calls, _ = _install_mocks(monkeypatch)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    try:
        setup_tab = window.tab("setup")
        assert setup_tab.otpTable.rowCount() == 0
        window.navigationInterface.widget("keys").clicked.emit(True)
        qtbot.waitUntil(
            lambda: window.stackedWidget.currentWidget() is window.tab("keys"),
            timeout=3000,
        )
        window.navigationInterface.widget("setup").clicked.emit(True)
        qtbot.waitUntil(
            lambda: setup_tab.otpTable.rowCount() == len(fc.OTP_FLAG_FIELDS),
            timeout=5000,
        )
    finally:
        window.close()
