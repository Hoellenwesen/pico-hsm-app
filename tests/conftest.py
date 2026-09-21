"""conftest.py — gemeinsame Fixtures (Home- + Vault-Isolation).

- Home: `PICO_HSM_HOME` zeigt für die gesamte Session auf ein leeres
  Temp-Verzeichnis (wird beim Import gesetzt, VOR allen App-Modulen —
  deren Pfad-Konstanten werten es genau einmal aus). Kein Test schreibt
  je ins echte `~/.pico_hsm` (Audit-Log, Drill-Protokoll, GUI-Config,
  Wizard-State). Cleanup per atexit.
- Vault: globales Singleton — jeder Test startet und endet gesperrt,
  damit keine PIN zwischen Tests überlebt.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile

import pytest

_TEST_HOME = tempfile.mkdtemp(prefix="pico-hsm-test-home-")
os.environ.setdefault("PICO_HSM_HOME", _TEST_HOME)


def _cleanup_test_home() -> None:
    shutil.rmtree(_TEST_HOME, ignore_errors=True)


atexit.register(_cleanup_test_home)


@pytest.fixture(autouse=True)
def _locked_vault():
    from gui.pin_vault import vault

    vault.lock()
    yield
    vault.lock()


@pytest.fixture
def test_home() -> str:
    """Isoliertes Home-Verzeichnis dieser Session (nur zur Kontrolle)."""
    return _TEST_HOME
