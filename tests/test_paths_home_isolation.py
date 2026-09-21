"""
test_paths_home_isolation.py — kein Test schreibt ins echte Home.

Das Session-Home (`PICO_HSM_HOME`, siehe tests/conftest.py) fängt alle
App-Ablagen ab (Audit-Log, Drill-Protokoll, GUI-Config, Wizard-State).
"""

from __future__ import annotations

import os
from pathlib import Path

from pico_hsm_tools import paths


def test_base_dir_honors_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PICO_HSM_HOME", str(tmp_path))
    assert paths.base_dir() == tmp_path


def test_base_dir_defaults_to_dot_pico_hsm(monkeypatch):
    monkeypatch.delenv("PICO_HSM_HOME", raising=False)
    assert paths.base_dir() == Path.home() / ".pico_hsm"


def test_constants_point_into_isolated_home(test_home):
    from gui import config as gui_config
    from pico_hsm_tools import backup_core as bc
    from pico_hsm_tools import backup_index as bi
    from pico_hsm_tools import flash_core as fc

    home = Path(test_home)
    assert Path(str(fc.AUDIT_LOG)).is_relative_to(home)
    assert Path(str(fc.TOTP_SECRET_FILE)).is_relative_to(home)
    assert Path(str(bc.DRILL_LOG)).is_relative_to(home)
    assert Path(str(bi.DRILL_LOG)).is_relative_to(home)
    assert gui_config.default_config_path().is_relative_to(home)


def test_audit_append_stays_isolated(test_home):
    from pico_hsm_tools import flash_core as fc

    real_log = Path.home() / ".pico_hsm" / "update_audit.jsonl"
    before = real_log.stat().st_mtime_ns if real_log.exists() else None
    fc.append_audit({"file": "test.bin", "sha256": "ab" * 32,
                     "status": "isoliert"})
    assert Path(str(fc.AUDIT_LOG)).read_text(
        encoding="utf-8").rstrip().endswith("}")
    after = real_log.stat().st_mtime_ns if real_log.exists() else None
    assert after == before  # echtes Home unberührt
    assert fc.verify_audit_chain() is True


def test_wizard_state_stays_isolated(test_home, monkeypatch, tmp_path):
    from gui.tabs import wizard_tab as wiz_mod

    # Serial-Datei-Logik nutzt base_dir() — Defaults zeigen ins Test-Home.
    assert str(wiz_mod.STATE_FILE).startswith(str(Path(test_home)))
