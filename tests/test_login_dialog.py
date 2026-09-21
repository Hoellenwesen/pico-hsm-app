"""
test_login_dialog.py — Login-Dialog ohne Display und ohne Hardware.

Token-Liste, PIN-Flags und Login-Verifikation werden gemockt, Worker
laufen echt. Deckt ab: Aufbau, Board-Suche, Neu-vs-Bekannt-Panels,
Entsperren (Erfolg/Fehler), Assistent- und Nur-Ansehen-Wege.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from contextlib import contextmanager

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog
from qfluentwidgets import InfoBar

from gui.login_dialog import LoginDialog
from gui.pin_vault import vault
from pico_hsm_tools import flash_core as fc


def _tokens():
    return [
        {"label": "Neu-Board", "model": "PicoHSM", "serial": "FRESH1"},
        {"label": "Mein-Token", "model": "PicoHSM", "serial": "SN2"},
    ]


def _install_mocks(monkeypatch, flags=None, login_error=None):
    calls: dict = {"logins": []}

    def fake_flags(lib_path=None, serial=None):
        if flags is not None:
            return flags
        return {
            "login_required": True,
            "user_pin_initialized": True,
            "user_pin_count_low": False,
            "user_pin_final_try": False,
            "user_pin_locked": False,
            "user_pin_to_be_changed": False,
        }

    @contextmanager
    def fake_session(user_pin=None, serial=None):
        calls["logins"].append((user_pin, serial))
        if login_error is not None:
            raise login_error
        yield object()

    import gui.login_dialog as login_mod

    monkeypatch.setattr(login_mod, "list_tokens", lambda *a, **k: _tokens())
    monkeypatch.setattr(login_mod.pc, "read_pin_flags", fake_flags)
    monkeypatch.setattr(login_mod, "read_only_session", fake_session)
    monkeypatch.setattr(
        fc, "check_picotool_available",
        lambda: (_ for _ in ()).throw(fc.FlashError("kein picotool")),
    )
    return calls


def _make_dialog(qtbot, monkeypatch, **kwargs):
    calls = _install_mocks(monkeypatch, **kwargs)
    dialog = LoginDialog()
    qtbot.addWidget(dialog)
    dialog.show()
    return dialog, calls


def _bar_texts(dialog) -> str:
    return "\n".join(
        bar.titleLabel.text() + " " + bar.contentLabel.text()
        for bar in dialog.findChildren(InfoBar)
    )


# --- Aufbau --------------------------------------------------------------------

def test_builds_and_lists_boards(qtbot, monkeypatch):
    dialog, _ = _make_dialog(qtbot, monkeypatch)
    try:
        for name in (
            "boardCombo", "boardRefreshButton", "loginNextButton",
            "loginStatusLabel", "newBoardBox", "pinBox", "loginPinEdit",
            "unlockButton", "wizardButton",
            "loginCancelButton",
        ):
            assert dialog.findChild(object, name) is not None, name
        assert dialog.boardCombo.count() == 2
        assert "2 Board(s)" in dialog.statusLabel.text()
        assert not dialog.newBoardBox.isVisible()
        assert not dialog.pinBox.isVisible()
    finally:
        dialog.close()


# --- Neu vs. bekannt -------------------------------------------------------------

def test_fresh_board_shows_wizard_panel(qtbot, monkeypatch):
    dialog, _ = _make_dialog(
        qtbot, monkeypatch,
        flags={
            "login_required": True, "user_pin_initialized": False,
            "user_pin_count_low": False, "user_pin_final_try": False,
            "user_pin_locked": False, "user_pin_to_be_changed": False,
        },
    )
    try:
        qtbot.mouseClick(dialog.nextButton, Qt.LeftButton)
        assert dialog.newBoardBox.isVisible()
        assert not dialog.pinBox.isVisible()
        qtbot.mouseClick(dialog.wizardButton, Qt.LeftButton)
        assert dialog.result.open_wizard is True
        assert dialog.result.serial == "FRESH1"
        assert dialog.result.authenticated is False
    finally:
        dialog.close()


def test_known_board_shows_pin_panel(qtbot, monkeypatch):
    dialog, _ = _make_dialog(qtbot, monkeypatch)
    try:
        dialog.boardCombo.setCurrentIndex(1)
        qtbot.mouseClick(dialog.nextButton, Qt.LeftButton)
        assert dialog.pinBox.isVisible()
        assert not dialog.newBoardBox.isVisible()
    finally:
        dialog.close()


def test_locked_board_hints_sopin(qtbot, monkeypatch):
    dialog, _ = _make_dialog(
        qtbot, monkeypatch,
        flags={
            "login_required": True, "user_pin_initialized": True,
            "user_pin_count_low": False, "user_pin_final_try": False,
            "user_pin_locked": True, "user_pin_to_be_changed": False,
        },
    )
    try:
        qtbot.mouseClick(dialog.nextButton, Qt.LeftButton)
        assert "SO-PIN" in dialog.statusLabel.text()
    finally:
        dialog.close()


# --- Entsperren --------------------------------------------------------------------

def test_unlock_success_caches_and_accepts(qtbot, monkeypatch):
    dialog, calls = _make_dialog(qtbot, monkeypatch)
    try:
        dialog.boardCombo.setCurrentIndex(1)
        qtbot.mouseClick(dialog.nextButton, Qt.LeftButton)
        dialog.pinEdit.setText("1234")
        qtbot.mouseClick(dialog.unlockButton, Qt.LeftButton)
        qtbot.waitUntil(lambda: not dialog.isVisible(), timeout=5000)
        assert calls["logins"] == [("1234", "SN2")]
        assert vault.get() == "1234"
        assert dialog.result.authenticated is True
        assert dialog.result.serial == "SN2"
    finally:
        dialog.close()


def test_unlock_failure_stays(qtbot, monkeypatch):
    from pkcs11.exceptions import PinIncorrect

    dialog, _ = _make_dialog(
        qtbot, monkeypatch, login_error=PinIncorrect("falsch"),
    )
    try:
        qtbot.mouseClick(dialog.nextButton, Qt.LeftButton)
        dialog.pinEdit.setText("0000")
        qtbot.mouseClick(dialog.unlockButton, Qt.LeftButton)
        qtbot.waitUntil(
            lambda: bool(dialog.findChildren(InfoBar)), timeout=5000,
        )
        assert dialog.isVisible()
        assert vault.get() is None
        assert "fehlgeschlagen" in _bar_texts(dialog)
    finally:
        dialog.close()


# --- Ausgänge -------------------------------------------------------------------------


def test_cancel_rejects(qapp, qtbot, monkeypatch):
    dialog, _ = _make_dialog(qtbot, monkeypatch)
    try:
        dialog.reject()
        assert dialog.result.authenticated is False
    finally:
        dialog.close()


def test_next_without_board_errors(qtbot, monkeypatch):
    import gui.login_dialog as login_mod

    monkeypatch.setattr(login_mod, "list_tokens", lambda *a, **k: [])
    dialog = LoginDialog()
    qtbot.addWidget(dialog)
    dialog.show()
    try:
        qtbot.mouseClick(dialog.nextButton, Qt.LeftButton)
        qtbot.waitUntil(
            lambda: bool(dialog.findChildren(InfoBar)), timeout=5000,
        )
        assert "wählen" in _bar_texts(dialog)
    finally:
        dialog.close()
