#!/usr/bin/env python3
"""hw_second_accessor.py - Zweit-Zugriff bei gehaltener Session (Phase 1b/2.6).

Läuft in Fenster 2, während `tools/hw_hold_session.py` in Fenster 1
die exklusive Session hält. Protokolliert für BEIDE Zugriffsarten die
exakte Exception-Klasse + Meldung (§12-Punkte Sharing-Mode und
Belegt-Exception):

  a) zweite exklusive PKCS#11-Session öffnen (braucht User-PIN),
  b) pyscard-APDU (Datetime-GET, braucht KEINE PIN).

Schreibt nichts, ändert nichts - nur öffnen/beobachten/schließen.
Ausgabe auf stdout (in hw-logs/ umleiten).

Aufruf:  python tools/hw_second_accessor.py [--pin-env VAR] [--pkcs11-lib PFAD]
"""

from __future__ import annotations

import argparse
import os
import sys
from getpass import getpass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pico_hsm_tools import apdu_core as ac  # noqa: E402
from pico_hsm_tools.pkcs11_session import exclusive_session  # noqa: E402


def _report(title: str, func) -> None:
    print(f"--- {title} ---")
    try:
        func()
    except Exception as exc:  # noqa: BLE001 - Befund ist der Zweck
        print(f"FEHLER: {type(exc).__module__}.{type(exc).__name__}: {exc}")
    else:
        print("OK (kein Fehler)")
    print()


def _try_exclusive(pin: str, lib_path: str | None) -> None:
    def attempt():
        with exclusive_session(user_pin=pin, lib_path=lib_path):
            print("Session geöffnet UND GLEICH WIEDER GESCHLOSSEN.")

    _report("zweite exklusive PKCS#11-Session", attempt)


def _try_apdu() -> None:
    def attempt():
        conn = ac.open_connection()
        try:
            options = ac.get_dynamic_options(conn)
        finally:
            conn.disconnect()
        print(f"Dynamic Options gelesen: maske=0x{options.to_byte():02X} "
              f"(ptc={options.press_to_confirm}, "
              f"counter={options.key_usage_counter})")

    _report("pyscard-APDU bei offener PKCS#11-Session", attempt)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pin-env", default=None)
    parser.add_argument("--pkcs11-lib", default=None)
    args = parser.parse_args()

    pin = os.environ.get(args.pin_env) if args.pin_env else None
    if not pin:
        pin = getpass("User-PIN (nur Leseversuch, kein Write): ")
    if not pin:
        print("Keine PIN - Abbruch.")
        return 1

    print("=== Zweit-Zugriff bei gehaltener Session ===")
    print()
    _try_exclusive(pin, args.pkcs11_lib)
    _try_apdu()
    print("=== Ende (exakte Klassen+Meldungen oben in hw-logs sichern) ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
