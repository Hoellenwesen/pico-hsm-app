"""
test_hsm_backup.py — echtes HSM-Backup ohne Hardware und ohne Board.

Core (hsm_backup) mit gemockten Sessions/Tools. Deckt ab: DKEK-Gate,
Key-Reference-Auflösung, Export (Keys/Daten/Optionen), Versiegelung,
Restore-Anwendung + Verifikation.
"""

from __future__ import annotations

import json
import zipfile
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from pico_hsm_tools import hsm_backup as hb


def _inventory():
    return {
        "label": "T", "model": "M", "serial": "SN1",
        "objects": [
            {"label": "k1", "id": "01", "class": "PRIVATE_KEY",
             "type": "RSA"},
            {"label": "d1", "id": None, "class": "DATA", "type": None},
        ],
        "options": {"press_to_confirm": True, "key_usage_counter": False},
    }


# --- Gate --------------------------------------------------------------------------

def test_gate_aborts_without_dkek(monkeypatch):
    monkeypatch.setattr(
        hb.dc, "dkek_status", lambda: "Shares 0, CKV Nullen")
    with pytest.raises(hb.HsmBackupError, match="Kein DKEK"):
        hb.check_dkek_ready()


def test_gate_passes_on_unknown_status(monkeypatch):
    monkeypatch.setattr(hb.dc, "dkek_status", lambda: "irgendwas anderes")
    hb.check_dkek_ready()  # kein Marker -> weiter, Wrap entscheidet


def test_gate_maps_status_error(monkeypatch):
    def boom():
        raise RuntimeError("kein Board")

    monkeypatch.setattr(hb.dc, "dkek_status", boom)
    with pytest.raises(hb.HsmBackupError, match="nicht lesbar"):
        hb.check_dkek_ready()


# --- Reference-Auflösung -----------------------------------------------------------------

def test_resolve_override_wins():
    assert hb.resolve_key_reference("k1", listing="", overrides={"k1": 7}) == 7


def test_resolve_parses_listing():
    listing = "Private RSA Key [k1]\n  ID: 01\n  Reference: 3\n"
    assert hb.resolve_key_reference("k1", listing=listing) == 3


def test_resolve_missing_raises():
    with pytest.raises(hb.HsmBackupError, match="--key-ref"):
        hb.resolve_key_reference("unbekannt", listing="leer")


# --- Export ------------------------------------------------------------------------------

def _sessions(monkeypatch):
    @contextmanager
    def fake_ro(user_pin=None, lib_path=None, serial=None):
        yield SimpleNamespace()

    import pico_hsm_tools.pkcs11_session as ps

    monkeypatch.setattr(ps, "read_only_session", fake_ro)


def test_export_parts_full(monkeypatch, tmp_path):
    _sessions(monkeypatch)
    calls: dict = {"wraps": [], "reads": []}
    monkeypatch.setattr(
        hb.dc, "wrap_key",
        lambda out, ref, pin: calls["wraps"].append((out, ref, pin)))
    import pico_hsm_tools.objects_core as oc

    monkeypatch.setattr(
        oc, "read_data_object",
        lambda session, label: calls["reads"].append(label) or b"daten")
    monkeypatch.setattr(
        hb, "resolve_key_reference", lambda label, **k: 3)
    staging = tmp_path / "staging"
    manifest = hb.export_parts(
        _inventory(), ["keys", "data", "options"], staging, "1234")
    assert [k["label"] for k in manifest.keys] == ["k1"]
    assert manifest.keys[0]["reference"] == 3
    assert [d["label"] for d in manifest.data_objects] == ["d1"]
    assert manifest.options["press_to_confirm"] is True
    assert (staging / "hsm-manifest.json").exists()
    assert (staging / "keys" / "k1.wrap").exists() is False  # Mock schreibt nichts
    assert calls["wraps"][0][1:] == (3, "1234")


def test_export_rejects_unknown_part(tmp_path):
    with pytest.raises(hb.HsmBackupError, match="Unbekannt"):
        hb.export_parts(_inventory(), ["quatsch"], tmp_path, "1234")


def test_export_wrap_failure_aborts(monkeypatch, tmp_path):
    _sessions(monkeypatch)

    def boom(out, ref, pin):
        raise RuntimeError("sc-hsm-tool meldet Fehler.")

    monkeypatch.setattr(hb.dc, "wrap_key", boom)
    monkeypatch.setattr(
        hb, "resolve_key_reference", lambda label, **k: 3)
    with pytest.raises(hb.HsmBackupError, match="Wrap für Key 'k1'"):
        hb.export_parts(_inventory(), ["keys"], tmp_path, "1234")


# --- Versiegeln/Öffnen -----------------------------------------------------------------------

def test_seal_and_open_roundtrip(monkeypatch, tmp_path):
    import pico_hsm_tools.backup_core as bc

    monkeypatch.setattr(
        bc, "split_backup",
        lambda export_file, out_dir, recipients: {
            "pubkey": "age1rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr", "mode": "recipients",
            "recipients": list(recipients)})
    manifest = hb.export_parts(
        _inventory(), ["options"], tmp_path / "staging", "1234")
    assert manifest.keys == [] and manifest.data_objects == []
    sealed = hb.seal_bundle(
        tmp_path / "staging", tmp_path / "out", ["age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"])
    assert sealed["pubkey"] == "age1rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr"
    assert not (tmp_path / "staging").exists()  # Staging weg nach Erfolg


def test_seal_rejects_empty_recipients(tmp_path):
    with pytest.raises(hb.HsmBackupError, match="Empfänger"):
        hb.seal_bundle(tmp_path, tmp_path / "out", [])


def test_open_sealed_maps_error(monkeypatch, tmp_path):
    import pico_hsm_tools.backup_core as bc

    def boom(backup_dir, output_file, identity_file):
        raise bc.BackupError("Entschlüsselung fehlgeschlagen.")

    monkeypatch.setattr(bc, "restore_backup", boom)
    with pytest.raises(hb.HsmBackupError, match="Entschlüsselung"):
        hb.open_sealed_bundle(tmp_path, tmp_path / "work",
                              tmp_path / "id.txt")


# --- Anwenden + Verifizieren ---------------------------------------------------------------------

def _bundle_dir(tmp_path):
    work = tmp_path / "apply"
    work.mkdir()
    manifest = hb.HsmManifest(
        created_at="t", source_label="T", source_serial="SN1",
        parts=["keys", "data", "options"],
        keys=[{"label": "k1", "reference": 3, "file": "keys/k1.wrap"}],
        data_objects=[{"label": "d1", "file": "data/d1.bin"}],
        options={"press_to_confirm": True, "key_usage_counter": False},
    )
    (work / "keys").mkdir()
    (work / "keys" / "k1.wrap").write_bytes(b"wrapped")
    (work / "data").mkdir()
    (work / "data" / "d1.bin").write_bytes(b"daten")
    (work / hb.MANIFEST_NAME).write_text(
        json.dumps(manifest.to_dict()), encoding="utf-8")
    bundle = tmp_path / "hsm-bundle.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        for path in sorted(work.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(tmp_path))
    return bundle


def test_apply_and_verify(monkeypatch, tmp_path):
    calls: dict = {"unwraps": [], "writes": [], "sets": []}
    monkeypatch.setattr(
        hb.dc, "unwrap_key",
        lambda wrapped, ref, pin, force=False: calls["unwraps"].append(
            (wrapped, ref, pin, force)))
    import pico_hsm_tools.objects_core as oc
    import pico_hsm_tools.pkcs11_session as ps

    @contextmanager
    def fake_exclusive(user_pin=None, lib_path=None, serial=None):
        yield SimpleNamespace()

    @contextmanager
    def fake_ro(user_pin=None, lib_path=None, serial=None):
        yield SimpleNamespace()

    monkeypatch.setattr(ps, "exclusive_session", fake_exclusive)
    monkeypatch.setattr(ps, "read_only_session", fake_ro)
    monkeypatch.setattr(
        oc, "write_data_object",
        lambda session, data, label, private=True: calls["writes"].append(
            (label, data)))
    monkeypatch.setattr(
        oc, "list_objects",
        lambda session: [SimpleNamespace(label="k1"),
                         SimpleNamespace(label="d1")])
    import pico_hsm_tools.apdu_core as ac

    monkeypatch.setattr(ac, "open_connection", lambda reader=None: SimpleNamespace(
        disconnect=lambda: None))
    monkeypatch.setattr(
        ac, "set_dynamic_options",
        lambda conn, opts: calls["sets"].append(opts))
    monkeypatch.setattr(
        ac, "get_dynamic_options",
        lambda conn: SimpleNamespace(
            press_to_confirm=True, key_usage_counter=False))

    bundle = _bundle_dir(tmp_path)
    report, manifest_dict = hb.apply_bundle(bundle, "1234", force=True)
    assert report["keys"] == ["k1"]
    assert report["data"] == ["d1"]
    assert report["options"] is True
    assert calls["unwraps"][0][1:] == (3, "1234", True)

    manifest = hb.HsmManifest.from_dict(manifest_dict)
    verification = hb.verify_against_manifest(manifest, "1234")
    assert verification == {"missing_keys": [], "missing_data": [],
                            "options_ok": True}


def test_apply_unwrap_failure_guides(monkeypatch, tmp_path):
    def boom(wrapped, ref, pin, force=False):
        raise RuntimeError("Not allowed")

    monkeypatch.setattr(hb.dc, "unwrap_key", boom)
    bundle = _bundle_dir(tmp_path)
    with pytest.raises(hb.HsmBackupError, match="demselben DKEK"):
        hb.apply_bundle(bundle, "1234")


def test_apply_quirk_success_despite_exit_code(monkeypatch, tmp_path):
    def boom_ok(wrapped, ref, pin, force=False):
        raise RuntimeError("Key successfully imported")

    monkeypatch.setattr(hb.dc, "unwrap_key", boom_ok)
    import pico_hsm_tools.objects_core as oc
    import pico_hsm_tools.pkcs11_session as ps

    @contextmanager
    def fake_ro(user_pin=None, lib_path=None, serial=None):
        yield SimpleNamespace()

    @contextmanager
    def fake_exclusive(user_pin=None, lib_path=None, serial=None):
        yield SimpleNamespace()

    monkeypatch.setattr(ps, "read_only_session", fake_ro)
    monkeypatch.setattr(ps, "exclusive_session", fake_exclusive)
    monkeypatch.setattr(
        oc, "list_objects",
        lambda session: [SimpleNamespace(label="k1")])
    monkeypatch.setattr(
        oc, "write_data_object", lambda *a, **k: None)
    import pico_hsm_tools.apdu_core as ac

    monkeypatch.setattr(ac, "open_connection", lambda reader=None: SimpleNamespace(
        disconnect=lambda: None))
    monkeypatch.setattr(ac, "set_dynamic_options", lambda *a, **k: None)
    bundle = _bundle_dir(tmp_path)
    report, _manifest = hb.apply_bundle(bundle, "1234")
    assert report["keys"] == ["k1"]
    assert len(report["quirks"]) == 1


def test_apply_quirk_absent_key_still_fails(monkeypatch, tmp_path):
    def boom_ok(wrapped, ref, pin, force=False):
        raise RuntimeError("Key successfully imported")

    monkeypatch.setattr(hb.dc, "unwrap_key", boom_ok)
    import pico_hsm_tools.objects_core as oc
    import pico_hsm_tools.pkcs11_session as ps

    @contextmanager
    def fake_ro(user_pin=None, lib_path=None, serial=None):
        yield SimpleNamespace()

    monkeypatch.setattr(ps, "read_only_session", fake_ro)
    monkeypatch.setattr(oc, "list_objects", lambda session: [])
    bundle = _bundle_dir(tmp_path)
    with pytest.raises(hb.HsmBackupError, match="demselben DKEK"):
        hb.apply_bundle(bundle, "1234")


# --- CLI -------------------------------------------------------------------------------

def test_cli_hsm_backup_full(monkeypatch, tmp_path):
    import os

    from click.testing import CliRunner

    from cli.commands import backup as backup_mod
    from cli.main import cli

    monkeypatch.setenv("PICO_TEST_PIN", "1234")
    monkeypatch.setattr(
        backup_mod.hb, "check_dkek_ready", lambda status=None: None)
    monkeypatch.setattr(
        backup_mod.hb, "collect_inventory", lambda *a, **k: _inventory())
    monkeypatch.setattr(
        backup_mod.hb, "export_parts",
        lambda inv, parts, staging, pin, *a, **k: SimpleNamespace(
            keys=[{"label": "k1"}], data_objects=[],
            to_dict=lambda: {}))
    monkeypatch.setattr(
        backup_mod.hb, "seal_bundle",
        lambda *a, **k: {"pubkey": "age1rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr",
                         "recipients": ["age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"]})
    result = CliRunner().invoke(
        cli, ["--pin-env", "PICO_TEST_PIN", "backup", "hsm-backup",
              str(tmp_path / "out"), "--recipient", "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"],
    )
    assert result.exit_code == 0, result.output
    assert "HSM-Backup" in result.output
    assert "Empfänger" in result.output


def test_cli_hsm_backup_dkek_abort(monkeypatch, tmp_path):
    from click.testing import CliRunner

    from cli.commands import backup as backup_mod
    from cli.main import cli

    monkeypatch.setenv("PICO_TEST_PIN", "1234")
    monkeypatch.setattr(
        backup_mod.hb, "check_dkek_ready",
        lambda status=None: (_ for _ in ()).throw(
            backup_mod.hb.HsmBackupError("Kein DKEK")))
    result = CliRunner().invoke(
        cli, ["--pin-env", "PICO_TEST_PIN", "backup", "hsm-backup",
              str(tmp_path / "out"), "--recipient", "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq",
              "--no-data", "--no-options"],
    )
    assert result.exit_code == 2
    assert "Kein DKEK" in result.output


def test_cli_hsm_restore_reports(monkeypatch, tmp_path):
    from click.testing import CliRunner

    from cli.commands import backup as backup_mod
    from cli.main import cli

    monkeypatch.setenv("PICO_TEST_PIN", "1234")
    (tmp_path / "b").mkdir()
    monkeypatch.setattr(
        backup_mod.hb, "open_sealed_bundle",
        lambda *a, **k: tmp_path / "b.zip")
    monkeypatch.setattr(
        backup_mod.hb, "apply_bundle",
        lambda *a, **k: ({"keys": ["k1"], "data": [],
                          "options": True, "missing": []}, {}))
    monkeypatch.setattr(
        backup_mod.hb, "verify_against_manifest",
        lambda *a, **k: {"missing_keys": [], "missing_data": [],
                         "options_ok": True})
    monkeypatch.setattr(
        backup_mod.hb, "HsmManifest", SimpleNamespace(
            from_dict=lambda raw: SimpleNamespace()))
    (tmp_path / "apply").mkdir()
    (tmp_path / "apply" / "hsm-manifest.json").write_text("{}")
    ident = tmp_path / "id.txt"
    ident.write_text("AGE-SECRET-KEY-1x\n")
    result = CliRunner().invoke(
        cli, ["--pin-env", "PICO_TEST_PIN", "backup", "hsm-restore",
              str(tmp_path / "b"), "--identity-file", str(ident),
              "--work-dir", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    assert "Wiederhergestellt" in result.output
