"""
test_gui_pin_tab.py — PIN-Tab-Tests ohne Display und ohne Hardware.

Core (pin_core) wird gemockt, Worker laufen echt. Deckt ab: Aufbau,
Status-Flags + kritische Warnung, Ändern/Entsperren-Erfolg (Felder
danach leer), Mismatch/Leer-Abweisung ohne Core-Aufruf, Fehlerpfade,
Kein-PIN-Leak in InfoBars, strikt verdeckte Felder (Auge versteckt),
Refresh-bei-Tab-Wechsel.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from qfluentwidgets import InfoBar, PasswordLineEdit

from gui.main_window import MainWindow
from gui.tabs import pin_tab as pin_mod
from pico_hsm_tools import pin_core as pc

HEALTHY_FLAGS = {
    "login_required": True,
    "user_pin_initialized": True,
    "user_pin_count_low": False,
    "user_pin_final_try": False,
    "user_pin_locked": False,
    "user_pin_to_be_changed": False,
}


def _install_mocks(monkeypatch, flags=None, change_error=None,
                   unblock_error=None):
    """Core mocken. Gibt calls-Dict zurück."""
    calls: dict = {"changes": [], "unblocks": []}

    def fake_change(old_pin, new_pin, lib_path=None, serial=None):
        if change_error is not None:
            raise change_error
        calls["changes"].append((old_pin, new_pin))

    def fake_unblock(so_pin, new_pin, lib_path=None, serial=None):
        if unblock_error is not None:
            raise unblock_error
        calls["unblocks"].append((so_pin, new_pin))

    monkeypatch.setattr(pc, "change_user_pin", fake_change)
    monkeypatch.setattr(pc, "unblock_user_pin", fake_unblock)
    monkeypatch.setattr(
        pc, "read_pin_flags",
        lambda lib_path=None, serial=None: (
            dict(HEALTHY_FLAGS) if flags is None else flags
        ),
    )
    return calls


def _make_tab(qtbot, monkeypatch, **kwargs):
    calls = _install_mocks(monkeypatch, **kwargs)
    from gui.pin_vault import vault

    vault.unlock("alt-1234")
    tab = pin_mod.PinTab()
    qtbot.addWidget(tab)
    tab.show()
    return tab, calls


def _bar_texts(tab) -> str:
    return "\n".join(
        bar.titleLabel.text() + " " + bar.contentLabel.text()
        for bar in tab.findChildren(InfoBar)
    )


# --- Aufbau --------------------------------------------------------------------

def test_builds_with_all_fields(qtbot, monkeypatch):
    tab, _ = _make_tab(qtbot, monkeypatch)
    try:
        for name in (
            "newPinEdit1", "newPinEdit2", "changeButton",
            "soPinEdit", "unblockNewPinEdit1", "unblockNewPinEdit2",
            "unblockButton", "pinStatusLabel", "pinRefreshButton",
        ):
            assert tab.findChild(object, name) is not None, name
        assert tab.findChild(object, "oldPinEdit") is None
        fields = tab.findChildren(PasswordLineEdit)
        assert len(fields) == 5
    finally:
        tab.close()


def test_pin_fields_strictly_masked(qtbot, monkeypatch):
    """Regressionstest für die Kein-Auge-Entscheidung."""
    tab, _ = _make_tab(qtbot, monkeypatch)
    try:
        for field in tab.findChildren(PasswordLineEdit):
            assert field.viewButton.isHidden(), field.objectName()
    finally:
        tab.close()


# --- Status ----------------------------------------------------------------------

def test_refresh_fills_flags_without_warning(qtbot, monkeypatch):
    tab, _ = _make_tab(qtbot, monkeypatch)
    try:
        tab.refresh()
        qtbot.waitUntil(
            lambda: "user_pin_locked" in tab.pinStatusLabel.text(),
            timeout=5000,
        )
        assert "False" in tab.pinStatusLabel.text()
        assert tab.findChildren(InfoBar) == []
    finally:
        tab.close()


def test_critical_flags_raise_warning(qtbot, monkeypatch):
    flags = dict(HEALTHY_FLAGS, user_pin_final_try=True, user_pin_locked=True)
    tab, _ = _make_tab(qtbot, monkeypatch, flags=flags)
    try:
        tab.refresh()
        qtbot.waitUntil(lambda: bool(tab.findChildren(InfoBar)), timeout=5000)
        assert "kritisch" in _bar_texts(tab)
        assert "entsperren" in _bar_texts(tab)
    finally:
        tab.close()


def test_flags_failure_shows_error(qtbot, monkeypatch):
    monkeypatch.setattr(
        pc, "read_pin_flags",
        lambda lib_path=None, serial=None: (_ for _ in ()).throw(
            RuntimeError("kein Board"),
        ),
    )
    monkeypatch.setattr(pc, "change_user_pin", lambda *a, **k: None)
    monkeypatch.setattr(pc, "unblock_user_pin", lambda *a, **k: None)
    tab = pin_mod.PinTab()
    qtbot.addWidget(tab)
    tab.show()
    try:
        tab.refresh()
        qtbot.waitUntil(lambda: bool(tab.findChildren(InfoBar)), timeout=5000)
        assert "Nicht lesbar" in tab.pinStatusLabel.text()
    finally:
        tab.close()


# --- Ändern ------------------------------------------------------------------------

def test_change_success_clears_fields_and_updates_vault(qtbot, monkeypatch):
    from gui.pin_vault import vault

    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        tab.newPinEdit1.setText("neu-5678")
        tab.newPinEdit2.setText("neu-5678")
        qtbot.mouseClick(tab.changeButton, Qt.LeftButton)
        qtbot.waitUntil(lambda: bool(tab.findChildren(InfoBar)), timeout=5000)
        assert calls["changes"] == [("alt-1234", "neu-5678")]
        assert tab.newPinEdit1.text() == ""
        assert tab.newPinEdit2.text() == ""
        assert vault.get() == "neu-5678"
        assert "erfolgreich" in _bar_texts(tab)
    finally:
        tab.close()


def test_change_locked_aborts(qtbot, monkeypatch):
    from gui.pin_vault import vault

    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        vault.lock()
        tab.newPinEdit1.setText("neu-5678")
        tab.newPinEdit2.setText("neu-5678")
        qtbot.mouseClick(tab.changeButton, Qt.LeftButton)
        qtbot.wait(300)
        assert calls["changes"] == []
        assert "Gesperrt" in _bar_texts(tab)
    finally:
        tab.close()


def test_change_mismatch_aborts_without_core_call(qtbot, monkeypatch):
    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        tab.newPinEdit1.setText("neu-1111")
        tab.newPinEdit2.setText("neu-2222")
        qtbot.mouseClick(tab.changeButton, Qt.LeftButton)
        qtbot.wait(300)
        assert calls["changes"] == []
        assert "stimmen nicht überein" in _bar_texts(tab)
    finally:
        tab.close()


def test_change_empty_fields_rejected(qtbot, monkeypatch):
    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        qtbot.mouseClick(tab.changeButton, Qt.LeftButton)
        qtbot.wait(300)
        assert calls["changes"] == []
        assert "ausfüllen" in _bar_texts(tab)
    finally:
        tab.close()


def test_change_failure_leaks_no_pin(qtbot, monkeypatch):
    """Eingegebene PINs dürfen in keiner InfoBar auftauchen."""
    tab, calls = _make_tab(
        qtbot, monkeypatch, change_error=pc.PinError("pkcs11-tool meldet Fehler."),
    )
    try:
        tab.newPinEdit1.setText("s3cr3t-neu")
        tab.newPinEdit2.setText("s3cr3t-neu")
        qtbot.mouseClick(tab.changeButton, Qt.LeftButton)
        qtbot.waitUntil(lambda: bool(tab.findChildren(InfoBar)), timeout=5000)
        texts = _bar_texts(tab)
        assert "s3cr3t-neu" not in texts
        assert "fehlgeschlagen" in texts
    finally:
        tab.close()


# --- Entsperren ----------------------------------------------------------------------

def test_unblock_success_clears_fields(qtbot, monkeypatch):
    from gui.pin_vault import vault

    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        tab.soPinEdit.setText("so-9999")
        tab.unblockNewPinEdit1.setText("neu-0000")
        tab.unblockNewPinEdit2.setText("neu-0000")
        qtbot.mouseClick(tab.unblockButton, Qt.LeftButton)
        qtbot.waitUntil(lambda: bool(tab.findChildren(InfoBar)), timeout=5000)
        assert calls["unblocks"] == [("so-9999", "neu-0000")]
        assert tab.soPinEdit.text() == ""
        assert vault.get() == "neu-0000"
        assert "zurückgesetzt" in _bar_texts(tab)
    finally:
        tab.close()


# --- Tab-Wechsel -----------------------------------------------------------------------

def test_switch_to_pin_triggers_refresh(qtbot, monkeypatch):
    _install_mocks(monkeypatch)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    try:
        pin_tab = window.tab("pin")
        assert "abgefragt" in pin_tab.pinStatusLabel.text()
        window.navigationInterface.widget("keys").clicked.emit(True)
        qtbot.waitUntil(
            lambda: window.stackedWidget.currentWidget() is window.tab("keys"),
            timeout=3000,
        )
        window.navigationInterface.widget("pin").clicked.emit(True)
        qtbot.waitUntil(
            lambda: "abgefragt" not in pin_tab.pinStatusLabel.text(),
            timeout=5000,
        )
    finally:
        window.close()
