"""paths.py — zentrale Ablageorte der App (eine Quelle der Wahrheit).

Alles App-lokale (Audit-Log, Drill-Protokoll, GUI-Config, TOTP-Secret,
Wizard-State/Backups) liegt unter einem Basisverzeichnis:

- Standard: `~/.pico_hsm`
- Override per Umgebungsvariable `PICO_HSM_HOME` (portable Nutzung;
  Tests isolieren damit das echte Home — siehe `tests/conftest.py`).

Regel: Kein Modul baut `Path.home() / ".pico_hsm"` mehr selbst, alle
nutzen `base_dir()`.
"""

from __future__ import annotations

import os
from pathlib import Path

HOME_ENV_VAR = "PICO_HSM_HOME"
DEFAULT_DIR_NAME = ".pico_hsm"


def base_dir() -> Path:
    """Basisverzeichnis (Override per Env, sonst `~/.pico_hsm`)."""
    override = os.environ.get(HOME_ENV_VAR, "").strip()
    if override:
        return Path(override)
    return Path.home() / DEFAULT_DIR_NAME
