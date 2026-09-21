#!/usr/bin/env python3
"""hw_hold_session.py - exklusive PKCS#11-Session HALTEN (Phase 1b/2.6).

Gegenstück zu hw_second_accessor.py: Dieser Prozess hält eine
schreibende Session offen, während im zweiten Fenster der Zugriffs-
versuch läuft. Beenden mit Enter oder Strg+C (Session wird sauber
geschlossen).

PIN: --pin-env VAR (empfohlen) oder interaktiver Prompt. PIN wird
nie ausgegeben/geloggt.

Aufruf:  python tools/hw_hold_session.py [--pin-env VAR] [--pkcs11-lib PFAD]
"""

from __future__ import annotations

import argparse
import os
import sys
from getpass import getpass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pico_hsm_tools.pkcs11_session import exclusive_session  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pin-env", default=None)
    parser.add_argument("--pkcs11-lib", default=None)
    args = parser.parse_args()

    pin = os.environ.get(args.pin_env) if args.pin_env else None
    if not pin:
        pin = getpass("User-PIN (wird gehalten, nichts wird geschrieben): ")
    if not pin:
        print("Keine PIN - Abbruch.")
        return 1

    try:
        with exclusive_session(user_pin=pin, lib_path=args.pkcs11_lib):
            print("HOLDING exklusive Session - jetzt Fenster 2 starten.")
            print("Beenden mit Enter (Session wird geschlossen).")
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                pass
    except Exception as exc:  # noqa: BLE001 - Befund, kein Crash
        print(f"Session konnte NICHT geöffnet werden: {type(exc).__name__}: {exc}")
        return 2
    print("Session geschlossen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
