#!/usr/bin/env python3
"""hw_apdu_after_login.py — APDU-Reads nach PKCS#11-Login (Phase 1b).

Hintergrund: Vendor-APDUs (Datetime/DynOpts-GET) antworten mit
SW=6982 (Security Status Not Satisfied) — auch nach `init`, auch das
dokumentierte Datetime-GET. Hypothese: Das Applet verlangt vorherige
PIN-Verifikation (per PKCS#11-Login), der Status gilt dann kartenweit.

Ablauf: User-PIN per Prompt (nie geloggt!), read-only Session mit
Login öffnen und OFFEN HALTEN, dann beide APDU-Reads im selben
Prozess. Ausgabe: SW-Ergebnisse je Schritt (für §12).

Aufruf:  python tools/hw_apdu_after_login.py [--pin-env VAR] [--pkcs11-lib PFAD]
"""

from __future__ import annotations

import argparse
import os
import sys
from getpass import getpass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pico_hsm_tools import apdu_core as ac  # noqa: E402
from pico_hsm_tools.pkcs11_session import read_only_session  # noqa: E402


def _try_apdu(label):
    try:
        conn = ac.open_connection()
        try:
            options = ac.get_dynamic_options(conn)
        finally:
            conn.disconnect()
        print(f"DynOpts-GET: OK (maske=0x{options.to_byte():02X}, "
              f"ptc={options.press_to_confirm}, "
              f"counter={options.key_usage_counter})")
    except Exception as exc:  # noqa: BLE001 — Befund ist der Zweck
        print(f"{label}-GET: {type(exc).__name__}: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pin-env", default=None)
    parser.add_argument("--pkcs11-lib", default=None)
    args = parser.parse_args()

    pin = os.environ.get(args.pin_env) if args.pin_env else None
    if not pin:
        pin = getpass("User-PIN (nur Login, kein Write): ")
    if not pin:
        print("Keine PIN — Abbruch.")
        return 1

    print("=== APDU nach PKCS#11-Login ===")
    try:
        with read_only_session(user_pin=pin, lib_path=args.pkcs11_lib):
            print("Login: OK (Session offen gehalten)")
            _try_apdu("dynopts")
    except Exception as exc:  # noqa: BLE001
        print(f"Login FEHLGESCHLAGEN: {type(exc).__name__}: {exc}")
        return 2
    print("=== Ende (Ergebnisse oben in hw-logs sichern, PIN nie enthalten) ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
