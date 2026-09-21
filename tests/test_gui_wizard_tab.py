"""
test_gui_wizard_tab.py â€” Wizard-Tab-Tests ohne Display und ohne Hardware.

Core (detect, init, PIN, DKEK, Backup/Drill) und Dialoge werden gemockt,
Worker laufen echt. Deckt ab: Aufbau, Schritt-Gating, State-Datei,
alle fÃ¼nf Aktionen (Erfolg/Validierung), Restart, Tab-SprÃ¼nge.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

from PySide6.QtCore import Qt
from qfluentwidgets import InfoBar

from gui.tabs import wizard_tab as wiz_mod
from pico_hsm_tools.device_mode import DeviceMode, DeviceState


def _detect_state(mode=DeviceMode.NORMAL):
    return DeviceState(
        mode=mode, picotool_ok=True, token_present=mode == DeviceMode.NORMAL,
        detail="test",
    )


def _install_mocks(monkeypatch, tmp_path, *, detect_mode=DeviceMode.NORMAL):
    calls: dict = {
        "inits": [], "pins": [], "dkeks": 0,
        "confirms": [],
    }
    monkeypatch.setattr(wiz_mod, "STATE_FILE", tmp_path / "setup_state.json")
    monkeypatch.setattr(
        wiz_mod.pc, "read_pin_flags",
        lambda lib_path=None, serial=None: {
            "login_required": True,
            "user_pin_initialized": False,
            "user_pin_count_low": False,
            "user_pin_final_try": False,
            "user_pin_locked": False,
            "user_pin_to_be_changed": False,
        },
    )
    monkeypatch.setattr(
        wiz_mod, "detect", lambda *a, **k: _detect_state(detect_mode),
    )
    monkeypatch.setattr(
        wiz_mod.ic, "initialize_token",
        lambda so_pin, user_pin, shares=None: calls["inits"].append(
            (so_pin, user_pin, shares),
        ),
    )
    monkeypatch.setattr(
        wiz_mod.pc, "change_user_pin",
        lambda old, new: calls["pins"].append((old, new)),
    )

    def fake_dkek():
        calls["dkeks"] += 1
        return "DKEK ok"

    monkeypatch.setattr(wiz_mod.dc, "dkek_status", fake_dkek)
    monkeypatch.setattr(
        wiz_mod, "confirm_destructive",
        lambda parent, title, text: calls["confirms"].append(title) or True,
    )
    return calls


def _make_tab(qtbot, monkeypatch, tmp_path, **kwargs):
    calls = _install_mocks(monkeypatch, tmp_path, **kwargs)
    tab = wiz_mod.WizardTab()
    qtbot.addWidget(tab)
    tab.show()
    return tab, calls


def _bar_texts(tab) -> str:
    return "\n".join(
        bar.titleLabel.text() + " " + bar.contentLabel.text()
        for bar in tab.findChildren(InfoBar)
    )


# --- Aufbau / Navigation -------------------------------------------------------

def test_builds_on_first_open_step(qtbot, monkeypatch, tmp_path):
    tab, _ = _make_tab(qtbot, monkeypatch, tmp_path)
    try:
        assert tab.pages.count() == 5
        assert tab.pages.currentIndex() == 0
        assert not tab.nextButton.isEnabled()
        assert not tab.backButton.isEnabled()
        for name in (
            "wizardDetectButton", "wizardInitButton", "wizardPinChangeButton",
            "wizardDkekQueryButton",
        ):
            assert tab.findChild(object, name) is not None, name
        assert tab.findChild(object, "wizardBackupCreateButton") is None
    finally:
        tab.close()


def test_state_file_resumes(qtbot, monkeypatch, tmp_path):
    (tmp_path / "setup_state.json").write_text(
        '{"done": {"detect": true, "init": true}}', encoding="utf-8",
    )
    tab, _ = _make_tab(qtbot, monkeypatch, tmp_path)
    try:
        assert tab.pages.currentIndex() == 2
    finally:
        tab.close()


def test_first_open_step_helper():
    assert wiz_mod.first_open_step({k: False for k in wiz_mod.STEP_KEYS}) == 0
    done = {k: True for k in wiz_mod.STEP_KEYS}
    assert wiz_mod.first_open_step(done) == 4
    partial = {k: False for k in wiz_mod.STEP_KEYS}
    partial["detect"] = True
    assert wiz_mod.first_open_step(partial) == 1


# --- Schritt 1: Erkennen ---------------------------------------------------------

def test_detect_success_enables_next(qtbot, monkeypatch, tmp_path):
    tab, _ = _make_tab(qtbot, monkeypatch, tmp_path)
    try:
        qtbot.mouseClick(tab.detectButton, Qt.LeftButton)
        qtbot.waitUntil(tab.nextButton.isEnabled, timeout=5000)
        assert "Normal" in tab.detectLabel.text()
    finally:
        tab.close()


def test_detect_no_board_stays(qtbot, monkeypatch, tmp_path):
    tab, _ = _make_tab(
        qtbot, monkeypatch, tmp_path, detect_mode=DeviceMode.KEIN_GERAET,
    )
    try:
        qtbot.mouseClick(tab.detectButton, Qt.LeftButton)
        qtbot.waitUntil(lambda: bool(tab.findChildren(InfoBar)), timeout=5000)
        assert not tab.nextButton.isEnabled()
    finally:
        tab.close()


# --- Schritt 2: Init ---------------------------------------------------------------

def test_init_validation_rejects(qtbot, monkeypatch, tmp_path):
    tab, calls = _make_tab(qtbot, monkeypatch, tmp_path)
    try:
        tab._goto(1)
        tab.soPinEdit.setText("kurz")
        tab.initPinEdit1.setText("1234")
        tab.initPinEdit2.setText("1234")
        qtbot.mouseClick(tab.initButton, Qt.LeftButton)
        qtbot.waitUntil(lambda: bool(tab.findChildren(InfoBar)), timeout=5000)
        assert calls["inits"] == []
        assert "16 Hex" in _bar_texts(tab)
    finally:
        tab.close()


def test_init_success(qtbot, monkeypatch, tmp_path):
    tab, calls = _make_tab(qtbot, monkeypatch, tmp_path)
    try:
        tab._goto(1)
        tab.soPinEdit.setText("A" * 16)
        tab.initPinEdit1.setText("123456")
        tab.initPinEdit2.setText("123456")
        qtbot.mouseClick(tab.initButton, Qt.LeftButton)
        qtbot.waitUntil(
            lambda: tab.pages.currentIndex() == 1 and tab.nextButton.isEnabled(),
            timeout=5000,
        )
        assert calls["inits"] == [("A" * 16, "123456", None)]
    finally:
        tab.close()


# --- Schritt 3: PIN ------------------------------------------------------------------

def test_pin_mismatch_rejects(qtbot, monkeypatch, tmp_path):
    tab, calls = _make_tab(qtbot, monkeypatch, tmp_path)
    try:
        tab._goto(2)
        tab.oldPinEdit.setText("123456")
        tab.newPinEdit1.setText("aaa")
        tab.newPinEdit2.setText("bbb")
        qtbot.mouseClick(tab.pinChangeButton, Qt.LeftButton)
        qtbot.waitUntil(lambda: bool(tab.findChildren(InfoBar)), timeout=5000)
        assert calls["pins"] == []
    finally:
        tab.close()


# --- Schritt 4: DKEK -------------------------------------------------------------------

def test_dkek_query_success(qtbot, monkeypatch, tmp_path):
    tab, calls = _make_tab(qtbot, monkeypatch, tmp_path)
    try:
        tab._goto(3)
        qtbot.mouseClick(tab.dkekQueryButton, Qt.LeftButton)
        qtbot.waitUntil(
            lambda: "DKEK ok" in tab.dkekStatusText.toPlainText(), timeout=5000,
        )
        assert calls["dkeks"] == 1
        assert tab.nextButton.isEnabled()
    finally:
        tab.close()


# --- Fertig / Restart -----------------------------------------------------------------------

def test_restart_resets(qtbot, monkeypatch, tmp_path):
    tab, _ = _make_tab(qtbot, monkeypatch, tmp_path)
    try:
        tab._mark_done("detect")
        assert tab.nextButton.isEnabled()
        tab._goto(4)
        qtbot.mouseClick(tab.restartButton, Qt.LeftButton)
        assert tab.pages.currentIndex() == 0
        assert not tab.nextButton.isEnabled()
    finally:
        tab.close()


# --- Board-State / Serial-Trennung ------------------------------------------------------------

def test_state_file_for_serial(tmp_path, monkeypatch):
    monkeypatch.setattr(wiz_mod, "STATE_FILE", tmp_path / "setup_state.json")
    assert wiz_mod.state_file_for(None).name == "setup_state.json"
    assert wiz_mod.state_file_for("ABC123").name == "setup_state_ABC123.json"
    assert wiz_mod.state_file_for("../x").name == "setup_state____x.json"


def test_board_completed_fresh(monkeypatch):
    monkeypatch.setattr(
        wiz_mod.pc, "read_pin_flags", lambda lib_path=None, serial=None: {
            "user_pin_initialized": False,
        },
    )
    assert wiz_mod.board_completed() == {
        "detect": False, "init": False, "pin": False,
        "dkek": False,
    }


def test_board_completed_initialized(monkeypatch):
    monkeypatch.setattr(
        wiz_mod.pc, "read_pin_flags", lambda lib_path=None, serial=None: {
            "user_pin_initialized": True,
        },
    )
    done = wiz_mod.board_completed("SN1")
    assert done["init"] is True
    assert done["pin"] is True  # optional bei eingerichtetem Board
    assert done["detect"] is False and done["dkek"] is False


def test_board_completed_no_board(monkeypatch):
    def boom(lib_path=None, serial=None):
        raise RuntimeError("kein Board")

    monkeypatch.setattr(wiz_mod.pc, "read_pin_flags", boom)
    assert all(not v for v in wiz_mod.board_completed().values())


def test_wizard_skips_done_steps(qtbot, monkeypatch, tmp_path):
    (tmp_path / "setup_state.json").write_text(
        '{"done": {"detect": true, "init": true, "pin": true}}',
        encoding="utf-8",
    )
    tab, _ = _make_tab(qtbot, monkeypatch, tmp_path)
    try:
        assert tab.pages.currentIndex() == 3  # dkek
        assert not tab.backButton.isEnabled()
        tab._mark_done("dkek")
        assert tab.nextButton.isEnabled()
        qtbot.mouseClick(tab.nextButton, Qt.LeftButton)
        assert tab.pages.currentIndex() == 4  # Fertig-Seite
        tab._on_back()
        assert tab.pages.currentIndex() == 4  # kein offener Schritt davor
    finally:
        tab.close()


def test_wizard_initialized_board_pin_optional(qtbot, monkeypatch, tmp_path):
    tab, _ = _make_tab(qtbot, monkeypatch, tmp_path)
    try:
        monkeypatch.setattr(
            wiz_mod.pc, "read_pin_flags", lambda lib_path=None, serial=None: {
                "user_pin_initialized": True,
            },
        )
        tab.refresh()  # Board-State neu vereinen
        assert tab._done["init"] is True
        assert tab._done["pin"] is True
    finally:
        tab.close()


def test_state_isolation_per_serial(qtbot, monkeypatch, tmp_path):
    from gui import config as gui_config

    monkeypatch.setattr(wiz_mod, "STATE_FILE", tmp_path / "setup_state.json")
    monkeypatch.setattr(gui_config.cfg.tokenSerial, "value", "AAA")
    tab, _ = _make_tab(qtbot, monkeypatch, tmp_path)
    try:
        tab._mark_done("detect")
        assert (tmp_path / "setup_state_AAA.json").exists()
        assert not (tmp_path / "setup_state.json").exists()
    finally:
        tab.close()