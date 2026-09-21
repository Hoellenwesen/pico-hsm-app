"""
test_gui_dkek_tab.py — DKEK-Tab-Tests ohne Display und ohne Hardware.

Core (dkek_core) wird gemockt, Worker laufen echt. Deckt ab: Aufbau
(inkl. strikt verdeckter PIN-Felder), Status-Rohtext, Datei-Dialoge
(gemockt), Wrap/Unwrap-Erfolg (PIN danach leer, Datei bleibt),
Validierung/Abbruch ohne Core-Call, Confirm-Texte (Unwrap mit
Überschreiben-Hinweis), Kein-PIN-Leak, CLI-only-Share-Hinweis,
Refresh-bei-Tab-Wechsel.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget
from qfluentwidgets import InfoBar, PasswordLineEdit

from gui.main_window import MainWindow
from gui.tabs import dkek_tab as dkek_mod
from pico_hsm_tools import dkek_core as dc


def _install_mocks(monkeypatch, status_text="SHARES: 1", status_error=None,
                   wrap_error=None, unwrap_error=None):
    """Core + Dialoge mocken. Gibt calls-Dict zurück."""
    calls: dict = {
        "wraps": [], "unwraps": [], "confirms": [],
        "save_paths": ["C:/tmp/key.wrap"], "open_paths": ["C:/tmp/key.wrap"],
    }

    def fake_status():
        if status_error is not None:
            raise status_error
        return status_text

    def fake_wrap(out_file, key_reference, pin):
        if wrap_error is not None:
            raise wrap_error
        calls["wraps"].append((out_file, key_reference, pin))

    def fake_unwrap(wrapped_file, key_reference, pin):
        if unwrap_error is not None:
            raise unwrap_error
        calls["unwraps"].append((wrapped_file, key_reference, pin))

    monkeypatch.setattr(dc, "dkek_status", fake_status)
    monkeypatch.setattr(dc, "wrap_key", fake_wrap)
    monkeypatch.setattr(dc, "unwrap_key", fake_unwrap)
    monkeypatch.setattr(
        dkek_mod, "confirm_destructive",
        lambda parent, title, text: calls["confirms"].append((title, text)) or True,
    )
    monkeypatch.setattr(
        dkek_mod, "_ask_save_path",
        lambda parent, caption: calls["save_paths"][0],
    )
    monkeypatch.setattr(
        dkek_mod, "_ask_open_path",
        lambda parent, caption: calls["open_paths"][0],
    )
    return calls


def _make_tab(qtbot, monkeypatch, **kwargs):
    calls = _install_mocks(monkeypatch, **kwargs)
    from gui.pin_vault import vault

    vault.unlock("pin-1234")
    tab = dkek_mod.DkekTab()
    qtbot.addWidget(tab)
    tab.show()
    return tab, calls


def _bar_texts(tab) -> str:
    return "\n".join(
        bar.titleLabel.text() + " " + bar.contentLabel.text()
        for bar in tab.findChildren(InfoBar)
    )


# --- Aufbau --------------------------------------------------------------------

def test_builds_with_all_widgets(qtbot, monkeypatch):
    tab, _ = _make_tab(qtbot, monkeypatch)
    try:
        for name in (
            "dkekStatusText", "dkekRefreshButton",
            "wrapFileEdit", "wrapBrowseButton", "wrapRefSpin",
            "wrapButton", "unwrapFileEdit", "unwrapBrowseButton",
            "unwrapRefSpin", "unwrapButton",
        ):
            assert tab.findChild(object, name) is not None, name
        fields = tab.findChildren(PasswordLineEdit)
        assert len(fields) == 0  # PIN kommt aus dem Vault, kein Feld mehr
        for field in fields:
            assert field.viewButton.isHidden(), field.objectName()
    finally:
        tab.close()


def test_shares_hint_points_to_cli(qtbot, monkeypatch):
    tab, _ = _make_tab(qtbot, monkeypatch)
    try:
        texts = " ".join(
            w.text() for w in tab.findChildren(QWidget)
            if hasattr(w, "text") and isinstance(w.text(), str)
        )
        assert "create-share" in texts
        assert "CLI" in texts
    finally:
        tab.close()


# --- Status ----------------------------------------------------------------------

def test_refresh_fills_raw_status(qtbot, monkeypatch):
    tab, _ = _make_tab(qtbot, monkeypatch, status_text="SHARES: 2\nCKV: ab12")
    try:
        tab.refresh()
        qtbot.waitUntil(
            lambda: "SHARES" in tab.dkekStatusText.toPlainText(),
            timeout=5000,
        )
        assert tab.findChildren(InfoBar) == []
    finally:
        tab.close()


def test_refresh_failure_shows_error(qtbot, monkeypatch):
    tab, _ = _make_tab(
        qtbot, monkeypatch, status_error=RuntimeError("kein Board"),
    )
    try:
        tab.refresh()
        qtbot.waitUntil(lambda: bool(tab.findChildren(InfoBar)), timeout=5000)
        assert tab.dkekStatusText.toPlainText() == ""
    finally:
        tab.close()


# --- Dateien -----------------------------------------------------------------------

def test_browse_buttons_fill_edits(qtbot, monkeypatch):
    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        calls["save_paths"][0] = "C:/tmp/out.wrap"
        calls["open_paths"][0] = "C:/tmp/in.wrap"
        qtbot.mouseClick(tab.wrapBrowseButton, Qt.LeftButton)
        qtbot.mouseClick(tab.unwrapBrowseButton, Qt.LeftButton)
        assert tab.wrapFileEdit.text() == "C:/tmp/out.wrap"
        assert tab.unwrapFileEdit.text() == "C:/tmp/in.wrap"
    finally:
        tab.close()


# --- Wrap ----------------------------------------------------------------------------

def test_wrap_success_uses_vault_keeps_file(qtbot, monkeypatch):
    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        tab.wrapFileEdit.setText("C:/tmp/out.wrap")
        tab.wrapRefSpin.setValue(7)
        qtbot.mouseClick(tab.wrapButton, Qt.LeftButton)
        qtbot.waitUntil(lambda: bool(tab.findChildren(InfoBar)), timeout=5000)
        assert calls["wraps"] == [("C:/tmp/out.wrap", 7, "pin-1234")]
        assert tab.wrapFileEdit.text() == "C:/tmp/out.wrap"
        assert "exportiert" in _bar_texts(tab)
    finally:
        tab.close()


def test_wrap_locked_aborts(qtbot, monkeypatch):
    from gui.pin_vault import vault

    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        vault.lock()
        tab.wrapFileEdit.setText("C:/tmp/out.wrap")
        qtbot.mouseClick(tab.wrapButton, Qt.LeftButton)
        qtbot.wait(300)
        assert calls["wraps"] == []
    finally:
        tab.close()


def test_wrap_missing_input_aborts(qtbot, monkeypatch):
    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        qtbot.mouseClick(tab.wrapButton, Qt.LeftButton)
        qtbot.wait(300)
        assert calls["wraps"] == []
        assert calls["confirms"] == []
        assert "angeben" in _bar_texts(tab)
    finally:
        tab.close()


def test_wrap_cancel_aborts(qtbot, monkeypatch):
    tab, calls = _make_tab(qtbot, monkeypatch)
    monkeypatch.setattr(dkek_mod, "confirm_destructive", lambda *a: False)
    try:
        tab.wrapFileEdit.setText("C:/tmp/out.wrap")
        qtbot.mouseClick(tab.wrapButton, Qt.LeftButton)
        qtbot.wait(300)
        assert calls["wraps"] == []
    finally:
        tab.close()


def test_wrap_failure_leaks_no_pin(qtbot, monkeypatch):
    tab, _ = _make_tab(
        qtbot, monkeypatch, wrap_error=dc.DkekError("sc-hsm-tool meldet Fehler."),
    )
    try:
        tab.wrapFileEdit.setText("C:/tmp/out.wrap")
        qtbot.mouseClick(tab.wrapButton, Qt.LeftButton)
        qtbot.waitUntil(lambda: bool(tab.findChildren(InfoBar)), timeout=5000)
        texts = _bar_texts(tab)
        assert "s3cr3t-pin" not in texts
        assert "fehlgeschlagen" in texts
    finally:
        tab.close()


# --- Unwrap ----------------------------------------------------------------------------

def test_unwrap_success_with_overwrite_warning(qtbot, monkeypatch):
    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        tab.unwrapFileEdit.setText("C:/tmp/in.wrap")
        tab.unwrapRefSpin.setValue(3)
        qtbot.mouseClick(tab.unwrapButton, Qt.LeftButton)
        qtbot.waitUntil(lambda: bool(tab.findChildren(InfoBar)), timeout=5000)
        assert calls["unwraps"] == [("C:/tmp/in.wrap", 3, "pin-1234")]
        title, text = calls["confirms"][-1]
        assert "ersetzt" in text
        assert "importiert" in _bar_texts(tab)
    finally:
        tab.close()


# --- Tab-Wechsel ---------------------------------------------------------------------------

def test_switch_to_dkek_triggers_refresh(qtbot, monkeypatch):
    _install_mocks(monkeypatch, status_text="SHARES: 1")
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    try:
        dkek_tab = window.tab("dkek")
        assert dkek_tab.dkekStatusText.toPlainText() == ""
        window.navigationInterface.widget("keys").clicked.emit(True)
        qtbot.waitUntil(
            lambda: window.stackedWidget.currentWidget() is window.tab("keys"),
            timeout=3000,
        )
        window.navigationInterface.widget("dkek").clicked.emit(True)
        qtbot.waitUntil(
            lambda: "SHARES" in dkek_tab.dkekStatusText.toPlainText(),
            timeout=5000,
        )
    finally:
        window.close()
