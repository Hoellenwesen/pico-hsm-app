"""
test_gui_backup_tab.py — Backup-Tab-Tests ohne Display und ohne Hardware.

Core (backup_core/backup_index) und Dialoge werden gemockt, Worker
laufen echt. Deckt ab: Aufbau, Split/Restore/Drill per Empfänger,
Liste (inkl. Hygiene), HSM-Backup (Vollbackup/Benutzerdefiniert),
Datei-Dialoge, Refresh-bei-Tab-Wechsel.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

from PySide6.QtCore import Qt
from qfluentwidgets import InfoBar

from gui.main_window import MainWindow
from gui.tabs import backup_tab as backup_mod
from pico_hsm_tools import backup_core as bc
from pico_hsm_tools import backup_index


def _install_mocks(monkeypatch, split_manifest=None,
                   drill_result=True, split_error=None, list_infos=None):
    """Core + Dialoge mocken. Gibt calls-Dict zurück."""
    calls: dict = {
        "splits": [], "restores": [], "drills": [],
        "lists": [],
    }

    def fake_split(export_file, out_dir, recipients):
        if split_error is not None:
            raise split_error
        calls["splits"].append((export_file, out_dir, recipients))
        return split_manifest or {"pubkey": "age1zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz", "mode": "recipients",
                                  "recipients": list(recipients)}

    def fake_restore(backup_dir, output_file, identity_file):
        calls["restores"].append((backup_dir, output_file, identity_file))
        return {"status": "ok"}

    def fake_drill(backup_dir, identity_file):
        calls["drills"].append((backup_dir, identity_file))
        if not drill_result:
            raise bc.BackupError("Drill fehlgeschlagen.")
        return drill_result

    monkeypatch.setattr(bc, "split_backup", fake_split)
    monkeypatch.setattr(bc, "restore_backup", fake_restore)
    monkeypatch.setattr(bc, "real_drill", fake_drill)
    monkeypatch.setattr(
        backup_index, "list_backups",
        lambda parent_dir: calls["lists"].append(parent_dir) or (
            list_infos if list_infos is not None else []
        ),
    )
    monkeypatch.setattr(
        backup_mod, "_ask_open_file", lambda parent, caption: "C:/tmp/in.bin",
    )
    monkeypatch.setattr(
        backup_mod, "_ask_save_file", lambda parent, caption: "C:/tmp/out.bin",
    )
    monkeypatch.setattr(
        backup_mod, "_ask_directory", lambda parent, caption: "C:/tmp/dir",
    )
    return calls


def _make_tab(qtbot, monkeypatch, **kwargs):
    calls = _install_mocks(monkeypatch, **kwargs)
    tab = backup_mod.BackupTab()
    qtbot.addWidget(tab)
    tab.show()
    return tab, calls


def _bar_texts(tab) -> str:
    return "\n".join(
        bar.titleLabel.text() + " " + bar.contentLabel.text()
        for bar in tab.findChildren(InfoBar)
    )


def _backup_info(**kwargs):
    defaults = {
        "path": "/backups/b1", "created_at": "2026-01-01",
        "threshold": 3, "total_shares": 5, "ciphertext_sha256": "ab12",
        "leftover_shares_file_present": False,
        "last_drill_at": None, "last_drill_result": None,
        "age_days": None, "drill_age_days": None, "drill_state": "nie",
        "problems": [],
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# --- Aufbau --------------------------------------------------------------------

def test_builds_with_defaults(qtbot, monkeypatch):
    tab, _ = _make_tab(qtbot, monkeypatch)
    try:
        for name in (
            "hsmAllCheck", "hsmKeysCheck", "hsmDataCheck", "hsmOptionsCheck",
            "hsmOutEdit", "hsmOutBrowseButton", "hsmRecipientsEdit",
            "hsmBackupButton",
            "hsmRestoreDirEdit", "hsmRestoreBrowseButton", "hsmForceCheck",
            "hsmRestoreIdentityEdit", "hsmRestoreIdentityBrowseButton",
            "hsmRestoreButton", "hsmResultLabel",
            "listDirEdit", "listDirBrowseButton",
            "backupRefreshButton", "backupsTable",
        ):
            assert tab.findChild(object, name) is not None, name
        assert tab.findChild(object, "splitButton") is None
        assert tab.findChild(object, "restoreButton") is None
        assert tab.findChild(object, "drillButton") is None
        assert tab.backupsTable.columnCount() == 6
    finally:
        tab.close()


# --- Splitten ----------------------------------------------------------------------







# --- Wiederherstellen -------------------------------------------------------------------





# --- Drill -------------------------------------------------------------------------------





# --- Liste -------------------------------------------------------------------------------

def test_list_fills_table_and_warns(qtbot, monkeypatch):
    infos = [
        _backup_info(),
        _backup_info(path="/backups/b2", leftover_shares_file_present=True),
    ]
    tab, _ = _make_tab(qtbot, monkeypatch, list_infos=infos)
    try:
        tab.listDirEdit.setText("C:/tmp")
        tab.refresh()
        qtbot.waitUntil(lambda: tab.backupsTable.rowCount() == 2, timeout=5000)
        assert tab.backupsTable.item(0, 2).text() == "3-von-5"
        assert "noch nie" in tab.backupsTable.item(0, 4).text()
        assert "DO-NOT-KEEP-TOGETHER" in _bar_texts(tab)
    finally:
        tab.close()


def test_list_hygiene_column_ok_and_warn(qtbot, monkeypatch):
    fresh = _backup_info(
        path="/backups/ok", created_at="2026-09-01",
        age_days=5, drill_age_days=2, drill_state="frisch",
        last_drill_at="2026-09-18", last_drill_result="PASS",
    )
    stale = _backup_info(
        path="/backups/alt", age_days=120, drill_state="nie",
        problems=["Ciphertext-Datei fehlt"],
    )
    tab, _ = _make_tab(qtbot, monkeypatch, list_infos=[fresh, stale])
    try:
        tab.listDirEdit.setText("C:/tmp")
        tab.refresh()
        qtbot.waitUntil(lambda: tab.backupsTable.rowCount() == 2, timeout=5000)
        assert tab.backupsTable.item(0, 5).text() == "OK"
        assert tab.backupsTable.item(1, 5).text().startswith("WARN")
        assert "vor 5 Tagen" in tab.backupsTable.item(0, 1).text()
        assert "Hygiene:" in _bar_texts(tab)
    finally:
        tab.close()


def test_browse_buttons_fill_edits(qtbot, monkeypatch):
    tab, _ = _make_tab(qtbot, monkeypatch)
    try:
        qtbot.mouseClick(tab.hsmOutBrowseButton, Qt.LeftButton)
        qtbot.mouseClick(tab.hsmRestoreBrowseButton, Qt.LeftButton)
        qtbot.mouseClick(tab.hsmRestoreIdentityBrowseButton, Qt.LeftButton)
        qtbot.mouseClick(tab.listDirBrowseButton, Qt.LeftButton)
        assert tab.hsmOutEdit.text() == "C:/tmp/dir"
        assert tab.hsmRestoreDirEdit.text() == "C:/tmp/dir"
        assert tab.hsmRestoreIdentityEdit.text() == "C:/tmp/in.bin"
        assert tab.listDirEdit.text() == "C:/tmp/dir"
    finally:
        tab.close()


# --- HSM-Backup (Vollbackup/Benutzerdefiniert) ----------------------------------------------

def test_hsm_widgets_exist(qtbot, monkeypatch):
    tab, _ = _make_tab(qtbot, monkeypatch)
    try:
        for name in (
            "hsmAllCheck", "hsmKeysCheck", "hsmDataCheck", "hsmOptionsCheck",
            "hsmOutEdit", "hsmOutBrowseButton",
            "hsmBackupButton", "hsmRestoreDirEdit", "hsmRestoreBrowseButton",
            "hsmForceCheck", "hsmRestoreIdentityEdit",
            "hsmRestoreIdentityBrowseButton", "hsmRestoreButton",
            "hsmResultLabel", "hsmRecipientsEdit",
        ):
            assert tab.findChild(object, name) is not None, name
    finally:
        tab.close()


def test_hsm_master_checkbox_sync(qtbot, monkeypatch):
    tab, _ = _make_tab(qtbot, monkeypatch)
    try:
        assert tab.hsmAllCheck.isChecked()
        tab.hsmDataCheck.setChecked(False)
        assert not tab.hsmAllCheck.isChecked()
        assert tab._hsm_parts() == ["keys", "options"]
        tab.hsmAllCheck.setChecked(True)
        assert tab._hsm_parts() == ["keys", "data", "options"]
    finally:
        tab.close()


def test_hsm_backup_flow(qtbot, monkeypatch):
    from gui.pin_vault import vault

    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        vault.unlock("1234")
        monkeypatch.setattr(
            backup_mod, "_do_hsm_backup",
            lambda parts, out, pin, serial, recipients: calls.setdefault(
                "hsm", []).append((parts, pin, recipients)) or {
                    "parts": parts, "keys": ["k1"], "data": [],
                    "pubkey": "age1rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr"},
        )
        tab.hsmOutEdit.setText("C:/tmp/hsm")
        tab.hsmRecipientsEdit.setPlainText("age1pppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppp\n")
        qtbot.mouseClick(tab.hsmBackupButton, Qt.LeftButton)
        qtbot.waitUntil(
            lambda: "HSM-Backup ok" in tab.hsmResultLabel.text(),
            timeout=5000,
        )
        assert calls["hsm"][0][0] == ["keys", "data", "options"]
        assert calls["hsm"][0][1] == "1234"
        assert calls["hsm"][0][2] == ["age1pppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppp"]
    finally:
        tab.close()


def test_hsm_backup_locked_aborts(qtbot, monkeypatch):
    from gui.pin_vault import vault

    tab, _ = _make_tab(qtbot, monkeypatch)
    try:
        vault.lock()
        tab.hsmOutEdit.setText("C:/tmp/hsm")
        tab.hsmRecipientsEdit.setPlainText("age1pppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppp\n")
        qtbot.mouseClick(tab.hsmBackupButton, Qt.LeftButton)
        qtbot.wait(300)
        assert "Gesperrt" in _bar_texts(tab)
    finally:
        tab.close()


def test_hsm_backup_custom_selection(qtbot, monkeypatch):
    from gui.pin_vault import vault

    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        vault.unlock("1234")
        monkeypatch.setattr(
            backup_mod, "_do_hsm_backup",
            lambda parts, out, pin, serial, recipients: {
                "parts": parts, "keys": [], "data": [], "pubkey": "x"},
        )
        tab.hsmKeysCheck.setChecked(False)
        tab.hsmDataCheck.setChecked(False)
        tab.hsmOutEdit.setText("C:/tmp/hsm")
        tab.hsmRecipientsEdit.setPlainText("age1pppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppp\n")
        qtbot.mouseClick(tab.hsmBackupButton, Qt.LeftButton)
        qtbot.waitUntil(
            lambda: "HSM-Backup ok" in tab.hsmResultLabel.text(),
            timeout=5000,
        )
        assert "options," in tab.hsmResultLabel.text()
    finally:
        tab.close()


def test_hsm_backup_recipients_passthrough(qtbot, monkeypatch):
    from gui.pin_vault import vault

    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        vault.unlock("1234")
        monkeypatch.setattr(
            backup_mod, "_do_hsm_backup",
            lambda parts, out, pin, serial, recipients: calls.setdefault(
                "hsm2", []).append(recipients) or {
                    "parts": parts,
                    "keys": [], "data": [], "pubkey": "age1rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr"},
        )
        tab.hsmOutEdit.setText("C:/tmp/hsm")
        tab.hsmRecipientsEdit.setPlainText("age1pppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppp\nage1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\n")
        qtbot.mouseClick(tab.hsmBackupButton, Qt.LeftButton)
        qtbot.waitUntil(
            lambda: "Empfänger" in tab.hsmResultLabel.text(),
            timeout=5000,
        )
        assert calls["hsm2"] == [["age1pppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppp", "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"]]
    finally:
        tab.close()


def test_hsm_restore_identity(qtbot, monkeypatch):
    from gui.pin_vault import vault

    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        vault.unlock("1234")
        seen: dict = {}
        monkeypatch.setattr(
            backup_mod, "_do_hsm_restore",
            lambda *a, **k: seen.update(identity=a[4]) or {
                "report": {"keys": [], "data": [], "options": False},
                "verification": {"missing_keys": [], "missing_data": [],
                                 "options_ok": True}},
        )
        tab.hsmRestoreDirEdit.setText("C:/tmp/hsm")
        tab.hsmRestoreIdentityEdit.setText("C:/tmp/id.txt")
        qtbot.mouseClick(tab.hsmRestoreButton, Qt.LeftButton)
        qtbot.waitUntil(
            lambda: "Restore ok" in tab.hsmResultLabel.text(),
            timeout=5000,
        )
        assert seen["identity"] == "C:/tmp/id.txt"
    finally:
        tab.close()


def test_hsm_backup_requires_recipients(qtbot, monkeypatch):
    from gui.pin_vault import vault

    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        vault.unlock("1234")
        tab.hsmOutEdit.setText("C:/tmp/hsm")
        qtbot.mouseClick(tab.hsmBackupButton, Qt.LeftButton)
        qtbot.wait(300)
        assert "Empfänger" in _bar_texts(tab)
    finally:
        tab.close()

# --- Tab-Wechsel -------------------------------------------------------------------------------

def test_switch_to_backup_triggers_refresh(qtbot, monkeypatch):
    _install_mocks(monkeypatch)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    try:
        backup_tab = window.tab("backup")
        backup_tab.listDirEdit.setText("C:/tmp")
        assert backup_tab.backupsTable.rowCount() == 0
        window.navigationInterface.widget("keys").clicked.emit(True)
        qtbot.waitUntil(
            lambda: window.stackedWidget.currentWidget() is window.tab("keys"),
            timeout=3000,
        )
        window.navigationInterface.widget("backup").clicked.emit(True)
        qtbot.waitUntil(
            lambda: "Keine Backup" in _bar_texts(backup_tab)
            or backup_tab.backupsTable.rowCount() > 0,
            timeout=5000,
        )
    finally:
        window.close()
