"""
test_pin_vault.py — PIN-Vault ohne Hardware (RAM-Cache, Timeout, Sperren).
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gui.pin_vault import PinVault, vault


def test_locked_initially(qapp):
    assert not vault.is_unlocked
    assert vault.get() is None


def test_unlock_get_relocks(qapp):
    local = PinVault()
    try:
        changes: list = []
        local.lockedChanged.connect(changes.append)
        assert not local.is_unlocked
        local.unlock("1234")
        assert local.is_unlocked
        assert local.get() == "1234"
        assert changes == [False]
        local.lock()
        assert not local.is_unlocked
        assert local.get() is None
        assert changes == [False, True]
        local.lock()  # doppelt sperren: kein zweites Signal
        assert changes == [False, True]
    finally:
        local.deleteLater()


def test_timeout_relocks(qtbot):
    local = PinVault(timeout_ms=120)
    try:
        locked: list = []
        local.lockedChanged.connect(locked.append)
        local.unlock("1234")
        assert local.is_unlocked
        qtbot.waitUntil(lambda: not local.is_unlocked, timeout=5000)
        assert local.get() is None
        assert locked == [False, True]
    finally:
        local.deleteLater()


def test_use_resets_timeout(qtbot):
    local = PinVault(timeout_ms=400)
    try:
        local.unlock("1234")
        qtbot.wait(250)
        assert local.get() == "1234"  # Nutzung setzt Uhr zurück
        qtbot.wait(250)
        assert local.is_unlocked  # 250ms seit Nutzung < 400ms
        qtbot.waitUntil(lambda: not local.is_unlocked, timeout=5000)
    finally:
        local.deleteLater()


def test_lock_button_locks_vault(qapp, qtbot, monkeypatch):
    from gui.main_window import MainWindow

    vault.unlock("1234")
    window = MainWindow()
    try:
        window._poller.stop()
        assert vault.is_unlocked
        nav_widget = window.navigationInterface.widget("lock")
        assert nav_widget is not None
        nav_widget.clicked.emit(True)
        assert not vault.is_unlocked
    finally:
        window._poller.stop()
        window.close()


def test_vault_pin_or_raise(qapp):
    from gui.session_helpers import VaultLockedError, _vault_pin_or_raise

    assert vault.get() is None
    try:
        _vault_pin_or_raise()
        raise AssertionError("müsste werfen")
    except VaultLockedError as exc:
        assert "Gesperrt" in str(exc)
    vault.unlock("1234")
    try:
        assert _vault_pin_or_raise() == "1234"
    finally:
        vault.lock()
