"""init_core.py — Token-Erstinitialisierung (eine Quelle der Wahrheit).

Wickelt `sc-hsm-tool --initialize` (SO-PIN, User-PIN, optional
DKEK-Shares/Retry/Label) für CLI (`init token`) und Wizard-Tab.
LÖSCHT alle Keys/Zertifikate/Dateien auf dem Token — Aufrufer
zeigen vorher einen Confirm-Dialog.
"""

from __future__ import annotations

import subprocess


class InitError(Exception):
    """Initialisierung fehlgeschlagen (Tool fehlt / Board antwortet nicht)."""


SUBPROCESS_TIMEOUT_S = 30


def initialize_token(
    so_pin: str,
    user_pin: str,
    dkek_shares: int | None = None,
    pin_retry: int | None = None,
    label: str | None = None,
) -> None:
    """Token initialisieren. Wirft InitError statt zu raten."""
    if not so_pin or not user_pin:
        raise InitError("SO-PIN und User-PIN sind erforderlich.")
    args = ["sc-hsm-tool", "--initialize", "--so-pin", so_pin, "--pin", user_pin]
    if dkek_shares is not None:
        args += ["--dkek-shares", str(dkek_shares)]
    if pin_retry is not None:
        args += ["--pin-retry", str(pin_retry)]
    if label:
        args += ["--label", label]
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_S,
        )
    except FileNotFoundError as exc:
        raise InitError("sc-hsm-tool nicht gefunden (Teil von OpenSC).") from exc
    except subprocess.TimeoutExpired as exc:
        raise InitError("sc-hsm-tool antwortet nicht (Timeout).") from exc
    if result.returncode != 0:
        raise InitError(
            result.stderr.strip() or "Initialisierung fehlgeschlagen."
        )
