"""
test_gui_backup_tab.py — Backup-Tab-Tests ohne Display und ohne Hardware.

Core (backup_core/backup_index) und Dialoge werden gemockt, Worker
laufen echt. Deckt ab: Aufbau (inkl. 3-von-5-Defaults), Split-Mapping
+ Validierung, Restore-Share-Parsing + Feld-Leerung, Drill
Selbsttest/echt (PASS/FAIL), Liste (inkl. Leftover-Warnung),
Datei-Dialoge, Refresh-bei-Tab-Wechsel.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace
from pathlib import Path

from PySide6.QtCore import Qt
from qfluentwidgets import InfoBar

from gui.main_window import MainWindow
from gui.tabs import backup_tab as backup_mod
from pico_hsm_tools import backup_core as bc
from pico_hsm_tools import backup_index


def _install_mocks(monkeypatch, split_manifest=None, self_test_result=True,
                   drill_result=True, split_error=None, list_infos=None):
    """Core + Dialoge mocken. Gibt calls-Dict zurück."""
    calls: dict = {
        "splits": [], "restores": [], "self_tests": 0, "drills": [],
        "lists": [],
    }

    def fake_split(export_file, out_dir, threshold, total, identity_file):
        if split_error is not None:
            raise split_error
        calls["splits"].append(
            (export_file, out_dir, threshold, total, identity_file),
        )
        return split_manifest or {"pubkey": "age1mock"}

    def fake_restore(backup_dir, output_file, shares):
        calls["restores"].append((backup_dir, output_file, shares))
        return {"status": "ok"}

    def fake_self_test():
        calls["self_tests"] += 1
        return self_test_result

    def fake_drill(backup_dir, shares):
        calls["drills"].append((backup_dir, shares))
        return drill_result

    monkeypatch.setattr(bc, "split_backup", fake_split)
    monkeypatch.setattr(bc, "restore_backup", fake_restore)
    monkeypatch.setattr(bc, "self_test", fake_self_test)
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
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# --- Aufbau --------------------------------------------------------------------

def test_builds_with_defaults(qtbot, monkeypatch):
    tab, _ = _make_tab(qtbot, monkeypatch)
    try:
        for name in (
            "splitFileEdit", "splitDirEdit", "thresholdSpin", "totalSpin",
            "identityFileEdit", "splitButton", "splitResultLabel",
            "restoreDirEdit", "restoreFileEdit", "restoreSharesEdit",
            "restoreButton", "selfTestButton", "drillDirEdit",
            "drillSharesEdit", "drillButton", "listDirEdit",
            "backupRefreshButton", "backupsTable",
        ):
            assert tab.findChild(object, name) is not None, name
        assert tab.thresholdSpin.value() == 3
        assert tab.totalSpin.value() == 5
        assert tab.backupsTable.columnCount() == 5
    finally:
        tab.close()


# --- Splitten ----------------------------------------------------------------------

def test_split_mapping_and_hint(qtbot, monkeypatch):
    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        tab.splitFileEdit.setText("C:/tmp/secret.bin")
        tab.splitDirEdit.setText("C:/tmp/backup")
        qtbot.mouseClick(tab.splitButton, Qt.LeftButton)
        qtbot.waitUntil(lambda: len(calls["splits"]) == 1, timeout=5000)
        export_file, out_dir, m, n, identity = calls["splits"][0]
        assert (export_file, out_dir, m, n, identity) == (
            Path("C:/tmp/secret.bin"), Path("C:/tmp/backup"), 3, 5, None,
        )
        assert "age1mock" in tab.splitResultLabel.text()
        assert "3-2-1" in tab.splitResultLabel.text()
        assert "erstellt" in _bar_texts(tab)
    finally:
        tab.close()


def test_split_rejects_bad_input(qtbot, monkeypatch):
    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        qtbot.mouseClick(tab.splitButton, Qt.LeftButton)  # alles leer
        qtbot.wait(300)
        tab.thresholdSpin.setValue(5)
        tab.totalSpin.setValue(3)
        tab.splitFileEdit.setText("C:/tmp/secret.bin")
        tab.splitDirEdit.setText("C:/tmp/backup")
        qtbot.mouseClick(tab.splitButton, Qt.LeftButton)  # m > n
        qtbot.wait(300)
        assert calls["splits"] == []
        assert "angeben" in _bar_texts(tab) or "größer" in _bar_texts(tab)
    finally:
        tab.close()


def test_split_failure_shows_error(qtbot, monkeypatch):
    tab, _ = _make_tab(
        qtbot, monkeypatch, split_error=bc.BackupError("boom"),
    )
    try:
        tab.splitFileEdit.setText("C:/tmp/secret.bin")
        tab.splitDirEdit.setText("C:/tmp/backup")
        qtbot.mouseClick(tab.splitButton, Qt.LeftButton)
        qtbot.waitUntil(lambda: bool(tab.findChildren(InfoBar)), timeout=5000)
        assert "boom" in _bar_texts(tab)
    finally:
        tab.close()


# --- Wiederherstellen -------------------------------------------------------------------

def test_restore_parses_and_clears_shares(qtbot, monkeypatch):
    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        tab.restoreDirEdit.setText("C:/tmp/backup")
        tab.restoreFileEdit.setText("C:/tmp/out.bin")
        tab.restoreSharesEdit.setPlainText("  aaa:11\n\nbbb:22\n")
        qtbot.mouseClick(tab.restoreButton, Qt.LeftButton)
        qtbot.waitUntil(lambda: len(calls["restores"]) == 1, timeout=5000)
        _, _, shares = calls["restores"][0]
        assert shares == ["aaa:11", "bbb:22"]
        assert tab.restoreSharesEdit.toPlainText() == ""
        assert "Wiederhergestellt" in _bar_texts(tab)
    finally:
        tab.close()


def test_restore_without_shares_aborts(qtbot, monkeypatch):
    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        tab.restoreDirEdit.setText("C:/tmp/backup")
        tab.restoreFileEdit.setText("C:/tmp/out.bin")
        qtbot.mouseClick(tab.restoreButton, Qt.LeftButton)
        qtbot.wait(300)
        assert calls["restores"] == []
        assert "Share" in _bar_texts(tab)
    finally:
        tab.close()


# --- Drill -------------------------------------------------------------------------------

def test_self_test_pass_and_fail(qtbot, monkeypatch):
    tab, _ = _make_tab(qtbot, monkeypatch)
    try:
        qtbot.mouseClick(tab.selfTestButton, Qt.LeftButton)
        qtbot.waitUntil(lambda: "bestanden" in _bar_texts(tab), timeout=5000)
    finally:
        tab.close()


def test_self_test_fail_shows_error(qtbot, monkeypatch):
    tab, _ = _make_tab(qtbot, monkeypatch, self_test_result=False)
    try:
        qtbot.mouseClick(tab.selfTestButton, Qt.LeftButton)
        qtbot.waitUntil(lambda: "FEHLGESCHLAGEN" in _bar_texts(tab), timeout=5000)
    finally:
        tab.close()


def test_real_drill_parses_and_clears(qtbot, monkeypatch):
    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        tab.drillDirEdit.setText("C:/tmp/backup")
        tab.drillSharesEdit.setPlainText("s1\ns2\ns3\n")
        qtbot.mouseClick(tab.drillButton, Qt.LeftButton)
        qtbot.waitUntil(lambda: len(calls["drills"]) == 1, timeout=5000)
        _, shares = calls["drills"][0]
        assert shares == ["s1", "s2", "s3"]
        assert tab.drillSharesEdit.toPlainText() == ""
        assert "bestanden" in _bar_texts(tab)
    finally:
        tab.close()


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


def test_browse_buttons_fill_edits(qtbot, monkeypatch):
    tab, _ = _make_tab(qtbot, monkeypatch)
    try:
        qtbot.mouseClick(tab.splitFileBrowseButton, Qt.LeftButton)
        qtbot.mouseClick(tab.splitDirBrowseButton, Qt.LeftButton)
        qtbot.mouseClick(tab.restoreFileBrowseButton, Qt.LeftButton)
        qtbot.mouseClick(tab.listDirBrowseButton, Qt.LeftButton)
        assert tab.splitFileEdit.text() == "C:/tmp/in.bin"
        assert tab.splitDirEdit.text() == "C:/tmp/dir"
        assert tab.restoreFileEdit.text() == "C:/tmp/out.bin"
        assert tab.listDirEdit.text() == "C:/tmp/dir"
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
