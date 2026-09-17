"""
test_gui_keys_tab.py — Keys-Tab-Tests ohne Display und ohne Hardware.

Sessions (gui/session_helpers-Opener) und Core (objects_core) werden
gemockt, Worker laufen echt. Deckt ab: Aufbau (inkl. Defaults),
Liste/Löschen (inkl. Confirm-Abbruch), Generate RSA/EC (Warnlabel,
Argument-Mapping, Progress), AES, Schreiben (private-Default),
Lesen (Hex + Speichern), Zufall, PIN-Pflicht, PIN-Leerung bei
hideEvent, Konflikt-Retry, DKEK-Sprung, Refresh-bei-Tab-Wechsel.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from contextlib import contextmanager
from types import SimpleNamespace

from PySide6.QtCore import Qt
from qfluentwidgets import InfoBar, PasswordLineEdit

from gui.main_window import MainWindow
from gui.tabs import keys_tab as keys_mod
from pico_hsm_tools import objects_core as oc
from pico_hsm_tools.pkcs11_session import SessionConflictError


def _objects():
    return [
        oc.ObjectInfo(
            label="key1", id=b"\x01", object_class="PRIVATE_KEY",
            key_type="RSA", key_length_bits=2048,
        ),
        oc.ObjectInfo(
            label="cert1", id=None, object_class="DATA",
            key_type=None, key_length_bits=None,
        ),
    ]


def _install_mocks(monkeypatch):
    """Sessions + Core + Dialoge mocken. Gibt calls-Dict zurück."""
    calls: dict = {
        "sessions": [], "lists": 0, "deletes": [], "gen_rsa": [],
        "gen_ec": [], "gen_aes": [], "writes": [], "reads": [],
        "randoms": [], "confirms": [], "conflicts": [],
    }

    @contextmanager
    def fake_exclusive(pin):
        calls["sessions"].append(("exclusive", pin))
        yield SimpleNamespace()

    @contextmanager
    def fake_read_only(pin=None):
        calls["sessions"].append(("read-only", pin))
        yield SimpleNamespace()

    monkeypatch.setattr(keys_mod, "open_exclusive_session", fake_exclusive)
    monkeypatch.setattr(keys_mod, "open_read_only_session", fake_read_only)
    monkeypatch.setattr(
        oc, "list_objects",
        lambda session: calls.update(lists=calls["lists"] + 1) or _objects(),
    )
    monkeypatch.setattr(
        oc, "delete_object",
        lambda session, label: calls["deletes"].append(label) or True,
    )
    monkeypatch.setattr(
        oc, "generate_rsa_keypair",
        lambda s, b, i, l: calls["gen_rsa"].append((b, i, l)) or _objects()[0],
    )
    monkeypatch.setattr(
        oc, "generate_ec_keypair",
        lambda s, c, i, l: calls["gen_ec"].append((c, i, l)) or _objects()[0],
    )
    monkeypatch.setattr(
        oc, "generate_aes_key",
        lambda s, n, i, l: calls["gen_aes"].append((n, i, l)) or _objects()[0],
    )
    monkeypatch.setattr(
        oc, "write_data_object",
        lambda s, d, l, id=None, private=True: calls["writes"].append(
            (d, l, id, private)) or _objects()[1],
    )
    monkeypatch.setattr(
        oc, "read_data_object",
        lambda s, l: calls["reads"].append(l) or b"\xde\xad\xbe\xef",
    )
    monkeypatch.setattr(
        oc, "generate_random",
        lambda s, n: calls["randoms"].append(n) or bytes(range(n)),
    )
    monkeypatch.setattr(
        keys_mod, "confirm_destructive",
        lambda parent, title, text: calls["confirms"].append(title) or True,
    )
    monkeypatch.setattr(
        keys_mod, "show_conflict",
        lambda parent, msg, allow_retry=True: calls["conflicts"].append(msg) or True,
    )
    return calls


def _make_tab(qtbot, monkeypatch):
    calls = _install_mocks(monkeypatch)
    tab = keys_mod.KeysTab()
    qtbot.addWidget(tab)
    tab.show()
    tab.pinEdit.setText("1234")
    return tab, calls


def _bar_texts(tab) -> str:
    return "\n".join(
        bar.titleLabel.text() + " " + bar.contentLabel.text()
        for bar in tab.findChildren(InfoBar)
    )


# --- Aufbau --------------------------------------------------------------------

def test_builds_with_all_widgets_and_defaults(qtbot, monkeypatch):
    tab, _ = _make_tab(qtbot, monkeypatch)
    try:
        for name in (
            "pinEdit", "objectsTable", "keysRefreshButton", "deleteButton",
            "gotoDkekButton", "genTypeCombo", "genBitsCombo", "genCurveCombo",
            "genIdEdit", "genLabelEdit", "generateButton", "slowWarningLabel",
            "generateProgress", "aesBitsCombo", "aesIdEdit", "aesLabelEdit",
            "aesButton", "writeFileEdit", "writeBrowseButton", "writeLabelEdit",
            "writeIdEdit", "privateCheck", "writeButton", "readLabelEdit",
            "readButton", "readSaveButton", "readPreview", "randomSpin",
            "randomButton", "randomHex",
        ):
            assert tab.findChild(object, name) is not None, name
        assert tab.privateCheck.isChecked() is True
        assert tab.genBitsCombo.currentText() == "2048"
        assert tab.randomSpin.maximum() == oc.MAX_RANDOM_BYTES
        assert tab.pinEdit.viewButton.isHidden()
    finally:
        tab.close()


def test_pin_cleared_on_hide(qtbot, monkeypatch):
    tab, _ = _make_tab(qtbot, monkeypatch)
    try:
        assert tab.pinEdit.text() == "1234"
        tab.hide()
        assert tab.pinEdit.text() == ""
    finally:
        tab.close()


# --- Liste/Löschen -----------------------------------------------------------------

def test_refresh_fills_table(qtbot, monkeypatch):
    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        tab.refresh()
        qtbot.waitUntil(lambda: tab.objectsTable.rowCount() == 2, timeout=5000)
        assert tab.objectsTable.item(0, 0).text() == "key1"
        assert tab.objectsTable.item(0, 1).text() == "01"
        assert tab.objectsTable.item(1, 0).text() == "cert1"
        assert ("read-only", "1234") in calls["sessions"]
    finally:
        tab.close()


def test_refresh_without_pin_aborts(qtbot, monkeypatch):
    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        tab.pinEdit.setText("")
        tab.refresh()
        qtbot.wait(300)
        assert calls["lists"] == 0
        assert "User-PIN" in _bar_texts(tab)
    finally:
        tab.close()


def test_delete_selected_with_confirm(qtbot, monkeypatch):
    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        tab.refresh()
        qtbot.waitUntil(lambda: tab.objectsTable.rowCount() == 2, timeout=5000)
        tab.objectsTable.selectRow(0)
        qtbot.mouseClick(tab.deleteButton, Qt.LeftButton)
        qtbot.waitUntil(lambda: len(calls["deletes"]) == 1, timeout=5000)
        assert calls["deletes"] == ["key1"]
        assert "gelöscht" in _bar_texts(tab)
    finally:
        tab.close()


def test_delete_cancel_aborts(qtbot, monkeypatch):
    tab, calls = _make_tab(qtbot, monkeypatch)
    monkeypatch.setattr(keys_mod, "confirm_destructive", lambda *a: False)
    try:
        tab.refresh()
        qtbot.waitUntil(lambda: tab.objectsTable.rowCount() == 2, timeout=5000)
        tab.objectsTable.selectRow(0)
        qtbot.mouseClick(tab.deleteButton, Qt.LeftButton)
        qtbot.wait(300)
        assert calls["deletes"] == []
    finally:
        tab.close()


def test_delete_without_selection_aborts(qtbot, monkeypatch):
    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        tab.refresh()
        qtbot.waitUntil(lambda: tab.objectsTable.rowCount() == 2, timeout=5000)
        tab.objectsTable.clearSelection()
        qtbot.mouseClick(tab.deleteButton, Qt.LeftButton)
        qtbot.wait(300)
        assert calls["deletes"] == []
        assert "auswählen" in _bar_texts(tab)
    finally:
        tab.close()


# --- Erzeugen --------------------------------------------------------------------------

def test_slow_warning_only_for_slow_rsa(qtbot, monkeypatch):
    tab, _ = _make_tab(qtbot, monkeypatch)
    try:
        assert tab.slowWarningLabel.text() != ""
        tab.genBitsCombo.setCurrentText("1024")
        assert tab.slowWarningLabel.text() == ""
        tab.genTypeCombo.setCurrentText("ec")
        assert tab.slowWarningLabel.text() == ""
        assert tab.genBitsCombo.isVisible() is False
        assert tab.genCurveCombo.isVisible() is True
    finally:
        tab.close()


def test_generate_rsa_mapping_and_progress(qtbot, monkeypatch):
    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        tab.genTypeCombo.setCurrentText("rsa")
        tab.genBitsCombo.setCurrentText("4096")
        tab.genIdEdit.setText("0a")
        tab.genLabelEdit.setText("myrsa")
        qtbot.mouseClick(tab.generateButton, Qt.LeftButton)
        qtbot.waitUntil(lambda: len(calls["gen_rsa"]) == 1, timeout=5000)
        assert calls["gen_rsa"] == [(4096, b"\x0a", "myrsa")]
        qtbot.waitUntil(
            lambda: tab.generateProgress.isVisible() is False, timeout=5000,
        )
        assert "erzeugt" in _bar_texts(tab)
        assert ("exclusive", "1234") in calls["sessions"]
    finally:
        tab.close()


def test_generate_ec_mapping(qtbot, monkeypatch):
    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        tab.genTypeCombo.setCurrentText("ec")
        tab.genCurveCombo.setCurrentText("secp256r1")
        tab.genIdEdit.setText("02")
        tab.genLabelEdit.setText("myec")
        qtbot.mouseClick(tab.generateButton, Qt.LeftButton)
        qtbot.waitUntil(lambda: len(calls["gen_ec"]) == 1, timeout=5000)
        assert calls["gen_ec"] == [("secp256r1", b"\x02", "myec")]
    finally:
        tab.close()


def test_generate_invalid_id_aborts(qtbot, monkeypatch):
    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        tab.genIdEdit.setText("zz")
        tab.genLabelEdit.setText("x")
        qtbot.mouseClick(tab.generateButton, Qt.LeftButton)
        qtbot.wait(300)
        assert calls["gen_rsa"] == [] and calls["gen_ec"] == []
        assert "Hex" in _bar_texts(tab)
    finally:
        tab.close()


def test_generate_aes_mapping(qtbot, monkeypatch):
    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        tab.aesBitsCombo.setCurrentText("128")
        tab.aesIdEdit.setText("03")
        tab.aesLabelEdit.setText("myaes")
        qtbot.mouseClick(tab.aesButton, Qt.LeftButton)
        qtbot.waitUntil(lambda: len(calls["gen_aes"]) == 1, timeout=5000)
        assert calls["gen_aes"] == [(16, b"\x03", "myaes")]
    finally:
        tab.close()


# --- Schreiben/Lesen -----------------------------------------------------------------------

def test_write_private_default_and_size(qtbot, monkeypatch, tmp_path):
    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        target = tmp_path / "cert.der"
        target.write_bytes(b"\x30\x82")
        tab.writeFileEdit.setText(str(target))
        tab.writeLabelEdit.setText("mycert")
        qtbot.mouseClick(tab.writeButton, Qt.LeftButton)
        qtbot.waitUntil(lambda: len(calls["writes"]) == 1, timeout=5000)
        data, label, id_bytes, private = calls["writes"][0]
        assert (data, label, id_bytes, private) == (b"\x30\x82", "mycert", None, True)
        assert "PIN-geschützt" in _bar_texts(tab)
    finally:
        tab.close()


def test_read_preview_and_save(qtbot, monkeypatch, tmp_path):
    tab, calls = _make_tab(qtbot, monkeypatch)
    monkeypatch.setattr(
        keys_mod, "_ask_save_path", lambda parent, caption: str(tmp_path / "out.bin"),
    )
    try:
        tab.readLabelEdit.setText("mycert")
        qtbot.mouseClick(tab.readButton, Qt.LeftButton)
        qtbot.waitUntil(
            lambda: tab.readPreview.toPlainText() != "", timeout=5000,
        )
        assert tab.readPreview.toPlainText() == "deadbeef"
        assert calls["reads"] == ["mycert"]
        qtbot.mouseClick(tab.readSaveButton, Qt.LeftButton)
        qtbot.waitUntil(
            lambda: "geschrieben" in _bar_texts(tab), timeout=5000,
        )
        assert (tmp_path / "out.bin").read_bytes() == b"\xde\xad\xbe\xef"
    finally:
        tab.close()


def test_random_hex_output(qtbot, monkeypatch):
    tab, calls = _make_tab(qtbot, monkeypatch)
    try:
        tab.randomSpin.setValue(4)
        qtbot.mouseClick(tab.randomButton, Qt.LeftButton)
        qtbot.waitUntil(
            lambda: tab.randomHex.toPlainText() != "", timeout=5000,
        )
        assert tab.randomHex.toPlainText() == "00010203"
        assert calls["randoms"] == [4]
    finally:
        tab.close()


# --- Konflikt / Navigation ---------------------------------------------------------------------

def test_conflict_retry_succeeds(qtbot, monkeypatch):
    tab, calls = _make_tab(qtbot, monkeypatch)
    attempts: list = []

    class _FlakyCM:
        def __init__(self, pin):
            self.pin = pin

        def __enter__(self):
            attempts.append(self.pin)
            if len(attempts) == 1:
                raise SessionConflictError("belegt")
            return SimpleNamespace()

        def __exit__(self, *exc):
            return False

    def flaky_cm(pin=None):
        return _FlakyCM(pin)

    monkeypatch.setattr(keys_mod, "open_read_only_session", flaky_cm)
    try:
        tab.refresh()
        qtbot.waitUntil(lambda: tab.objectsTable.rowCount() == 2, timeout=5000)
        assert len(attempts) == 2
        assert len(calls["conflicts"]) == 1
    finally:
        tab.close()


def test_goto_dkek_switches_tab(qtbot, monkeypatch):
    _install_mocks(monkeypatch)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    try:
        qtbot.mouseClick(window.tab("keys").gotoDkekButton, Qt.LeftButton)
        qtbot.waitUntil(
            lambda: window.stackedWidget.currentWidget() is window.tab("dkek"),
            timeout=3000,
        )
    finally:
        window.close()


def test_switch_to_keys_triggers_refresh(qtbot, monkeypatch):
    _install_mocks(monkeypatch)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    try:
        keys_tab = window.tab("keys")
        keys_tab.pinEdit.setText("1234")
        assert keys_tab.objectsTable.rowCount() == 0
        window.navigationInterface.widget("status").clicked.emit(True)
        qtbot.waitUntil(
            lambda: window.stackedWidget.currentWidget() is window.tab("status"),
            timeout=3000,
        )
        window.navigationInterface.widget("keys").clicked.emit(True)
        qtbot.waitUntil(
            lambda: keys_tab.objectsTable.rowCount() == 2, timeout=5000,
        )
    finally:
        window.close()
