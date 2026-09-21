"""
test_gui_firmware_tab.py — Firmware-Tab-Tests ohne Display und ohne Hardware.

Core (flash_core) und Dialoge werden gemockt, Worker laufen echt.
Deckt ab: Aufbau, Preflight-Erfolg/Fehler, Chain-Confirm, geführten
Flash-Ablauf (TOTP ok/ungültig/abgebrochen/fehlend, BOOTSEL-Abbruch,
rejected_totp-Audit, Progress-Log), Tab-Wechsel ohne Hardware-Zugriff.
Audit-Anzeige wohnt im Logs-Tab (tests/test_gui_logs_tab.py).
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

from PySide6.QtCore import Qt
from qfluentwidgets import InfoBar

from gui.main_window import MainWindow
from gui.tabs import firmware_tab as fw_mod
from pico_hsm_tools import flash_core as fc
from pico_hsm_tools import audit_log


def _preflight():
    return fc.PreflightResult(
        fw_path=Path("C:/tmp/fw.uf2"),
        sha256="ab" * 32,
        version=fc.Uf2Info(major=1, minor=0, rollback=3),
        board_fingerprint="FF" * 32,
        last_known_rollback=2,
    )


def _entries():
    return [
        audit_log.AuditEntry("2026-09-01T10:00:00", "flashed", {}),
    ]


class _FakeSecretFile:
    def __init__(self, exists=True):
        self._exists = exists

    def exists(self):
        return self._exists

    def read_text(self):
        return "JBSWY3DPEHPK3PXP"


def _install_mocks(monkeypatch, chain_intact=True, totp_exists=False,
                   totp_valid=True, preflight_error=None, preflight_result=None):
    """Core + Dialoge mocken. Gibt calls-Dict zurück."""
    calls: dict = {
        "preflights": [], "flashes": [], "totp_checks": [],
        "audits": [], "confirms": [], "totp_prompts": 0,
        "tails": [],
    }

    def fake_preflight(fw_path, expected_fingerprint=None):
        if preflight_error is not None:
            raise preflight_error
        calls["preflights"].append(fw_path)
        return preflight_result if preflight_result is not None else _preflight()

    def fake_flash(preflight, on_progress=None):
        calls["flashes"].append(preflight)
        if on_progress is not None:
            on_progress("Flashe über picotool ...")

    monkeypatch.setattr(fc, "run_preflight", fake_preflight)
    monkeypatch.setattr(fc, "do_flash", fake_flash)
    monkeypatch.setattr(
        fc, "verify_totp_code",
        lambda secret, code: calls["totp_checks"].append(code) or totp_valid,
    )
    monkeypatch.setattr(
        fc, "append_audit", lambda entry: calls["audits"].append(entry),
    )
    monkeypatch.setattr(fc, "verify_audit_chain", lambda: chain_intact)
    monkeypatch.setattr(
        fc, "TOTP_SECRET_FILE", _FakeSecretFile(totp_exists),
    )
    monkeypatch.setattr(
        audit_log, "tail_flash_audit_log",
        lambda limit=20: calls["tails"].append(limit) or _entries(),
    )
    monkeypatch.setattr(
        fw_mod, "confirm_destructive",
        lambda parent, title, text: calls["confirms"].append(title) or True,
    )
    monkeypatch.setattr(
        fw_mod, "_ask_open_file", lambda parent, caption: "C:/tmp/fw.uf2",
    )

    def fake_totp(parent):
        calls["totp_prompts"] += 1
        return "123456"

    monkeypatch.setattr(fw_mod, "_ask_totp_code", fake_totp)
    return calls


def _make_tab(qtbot, monkeypatch, **kwargs):
    calls = _install_mocks(monkeypatch, **kwargs)
    tab = fw_mod.FirmwareTab()
    qtbot.addWidget(tab)
    tab.show()
    tab.fwFileEdit.setText("C:/tmp/fw.uf2")
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
            "fwFileEdit", "fwBrowseButton", "preflightButton",
            "preflightResultLabel", "flashButton", "flashLog",
        ):
            assert tab.findChild(object, name) is not None, name
    finally:
        tab.close()


# --- Preflight ---------------------------------------------------------------------

def test_preflight_success_fills_labels(qtbot, monkeypatch):
    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        qtbot.mouseClick(tab.preflightButton, Qt.LeftButton)
        qtbot.waitUntil(
            lambda: "Signatur gültig" in tab.preflightResultLabel.text(),
            timeout=5000,
        )
        assert "1.0" in tab.preflightResultLabel.text()
        assert "rollback=3" in tab.preflightResultLabel.text()
        assert len(calls["preflights"]) == 1
    finally:
        tab.close()


def test_preflight_failure_shows_error(qtbot, monkeypatch):
    tab, _ = _make_tab(
        qtbot, monkeypatch, preflight_error=fc.FlashError("Signatur kaputt"),
    )
    try:
        tab.fwFileEdit.setText("C:/tmp/fw.uf2")
        qtbot.mouseClick(tab.preflightButton, Qt.LeftButton)
        qtbot.waitUntil(lambda: bool(tab.findChildren(InfoBar)), timeout=5000)
        assert "Signatur kaputt" in _bar_texts(tab)
    finally:
        tab.close()


def test_broken_chain_aborts_without_confirm(qtbot, monkeypatch):
    tab, calls = _make_tab(qtbot, monkeypatch, chain_intact=False)
    monkeypatch.setattr(fw_mod, "confirm_destructive", lambda *a: False)
    try:
        qtbot.mouseClick(tab.preflightButton, Qt.LeftButton)
        qtbot.wait(300)
        assert calls["preflights"] == []
    finally:
        tab.close()


# --- Flash ---------------------------------------------------------------------------

def test_flash_full_flow_without_totp(qtbot, monkeypatch):
    tab, calls = _make_tab(qtbot, monkeypatch, totp_exists=False)
    try:
        qtbot.mouseClick(tab.flashButton, Qt.LeftButton)
        qtbot.waitUntil(lambda: len(calls["flashes"]) == 1, timeout=5000)
        assert calls["totp_prompts"] == 0
        assert "Flashe über picotool" in tab.flashLog.toPlainText()
        qtbot.waitUntil(
            lambda: "erfolgreich geflasht" in _bar_texts(tab), timeout=5000,
        )
    finally:
        tab.close()


def test_flash_with_totp_success(qtbot, monkeypatch):
    tab, calls = _make_tab(qtbot, monkeypatch, totp_exists=True)
    try:
        qtbot.mouseClick(tab.flashButton, Qt.LeftButton)
        qtbot.waitUntil(lambda: len(calls["flashes"]) == 1, timeout=5000)
        assert calls["totp_checks"] == ["123456"]
        assert "TOTP-Autorisierung bestätigt" in _bar_texts(tab)
        assert not [a for a in calls["audits"]
                    if a.get("status") == "rejected_totp"]
    finally:
        tab.close()


def test_flash_totp_invalid_audits_and_aborts(qtbot, monkeypatch):
    tab, calls = _make_tab(
        qtbot, monkeypatch, totp_exists=True, totp_valid=False,
    )
    try:
        qtbot.mouseClick(tab.flashButton, Qt.LeftButton)
        qtbot.waitUntil(lambda: bool(tab.findChildren(InfoBar)), timeout=5000)
        assert calls["flashes"] == []
        rejected = [a for a in calls["audits"]
                    if a.get("status") == "rejected_totp"]
        assert len(rejected) == 1
        assert "ungültig" in _bar_texts(tab)
    finally:
        tab.close()


def test_flash_bootsel_cancel_aborts(qtbot, monkeypatch):
    tab, calls = _make_tab(qtbot, monkeypatch, totp_exists=False)
    confirms = []
    orig_confirm = fw_mod.confirm_destructive

    def selective_confirm(parent, title, text):
        confirms.append(title)
        return "BOOTSEL" not in text

    monkeypatch.setattr(fw_mod, "confirm_destructive", selective_confirm)
    try:
        qtbot.mouseClick(tab.flashButton, Qt.LeftButton)
        qtbot.waitUntil(lambda: len(calls["preflights"]) == 1, timeout=5000)
        qtbot.wait(500)
        assert calls["flashes"] == []
        assert any("Flashen" in t for t in confirms)
    finally:
        tab.close()


def test_preflight_skipped_gate_shows_warning(qtbot, monkeypatch):
    skipped = fc.PreflightResult(
        fw_path=Path("C:/tmp/fw.uf2"),
        sha256="ab" * 32,
        version=fc.Uf2Info(1, 0, 3),
        board_fingerprint="",
        last_known_rollback=2,
        secure_boot_enabled=False,
        fingerprint_checked=False,
        signature_checked=False,
    )
    _install_mocks(monkeypatch, preflight_result=skipped)
    tab = fw_mod.FirmwareTab()
    qtbot.addWidget(tab)
    tab.show()
    try:
        tab.fwFileEdit.setText("C:/tmp/fw.uf2")
        qtbot.mouseClick(tab.preflightButton, Qt.LeftButton)
        qtbot.waitUntil(
            lambda: "übersprungen" in tab.preflightResultLabel.text(),
            timeout=5000,
        )
        assert "Fingerprint stimmt" not in tab.preflightResultLabel.text()
        assert "unsignierte Firmware" in tab.preflightResultLabel.text()
    finally:
        tab.close()


def test_preflight_unknown_rollback_shows_hint(qtbot, monkeypatch):
    unknown = fc.PreflightResult(
        fw_path=Path("C:/tmp/fw.uf2"),
        sha256="ab" * 32,
        version=fc.Uf2Info(6, 6, -1),
        board_fingerprint="",
        last_known_rollback=-1,
        secure_boot_enabled=False,
        fingerprint_checked=False,
        signature_checked=False,
    )
    _install_mocks(monkeypatch, preflight_result=unknown)
    tab = fw_mod.FirmwareTab()
    qtbot.addWidget(tab)
    tab.show()
    try:
        tab.fwFileEdit.setText("C:/tmp/fw.uf2")
        qtbot.mouseClick(tab.preflightButton, Qt.LeftButton)
        qtbot.waitUntil(
            lambda: "unbekannt" in tab.preflightResultLabel.text(),
            timeout=5000,
        )
    finally:
        tab.close()


# --- Tab-Wechsel -----------------------------------------------------------------------------

def test_switch_to_firmware_triggers_no_hardware(qtbot, monkeypatch):
    """Firmware-Tab hat keinen Auto-Refresh: Tab-Wechsel löst weder
    Preflight noch Flash noch Audit-Zugriff aus (alles nur per Button)."""
    calls = _install_mocks(monkeypatch)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    try:
        window.navigationInterface.widget("keys").clicked.emit(True)
        qtbot.waitUntil(
            lambda: window.stackedWidget.currentWidget() is window.tab("keys"),
            timeout=3000,
        )
        window.navigationInterface.widget("firmware").clicked.emit(True)
        qtbot.waitUntil(
            lambda: window.stackedWidget.currentWidget()
            is window.tab("firmware"),
            timeout=3000,
        )
        qtbot.wait(500)
        assert calls["preflights"] == []
        assert calls["flashes"] == []
        assert calls["tails"] == []
    finally:
        window.close()
