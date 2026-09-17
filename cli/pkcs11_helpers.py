"""
pkcs11_helpers.py — CLI-spezifische Kopplung an pico_hsm_tools.pkcs11_session.

Bündelt für die CLI-Befehle:
  - `cli_exclusive_session`/`cli_read_only_session`: PIN aus CliContext
    ziehen, `SessionConflictError` sauber über `ctx.fail()` melden.
  - `warn_if_gateway_reachable`: gemeinsamer, rein informativer Hinweis
    für subprocess-basierte Befehle (init.py/pin.py rufen sc-hsm-tool/
    pkcs11-tool auf, nicht python-pkcs11 direkt) — die bekommen den
    harten Konflikt-Check aus `exclusive_session()` nicht automatisch,
    da sie keine eigene PKCS#11-Session öffnen. Vorher als
    `_check_daemon_guard` in init.py UND pin.py dupliziert, jetzt hier
    konsolidiert (eine Quelle der Wahrheit).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from pico_hsm_tools.pkcs11_session import (
    SessionConflictError, check_gateway_reachable, exclusive_session,
    read_only_session,
)

from .context import CliContext


@contextmanager
def cli_exclusive_session(ctx: CliContext) -> Iterator["pkcs11.Session"]:  # noqa: F821
    """Öffnet eine schreibende Session.

    Bei `SessionConflictError` (der Reader war beim tatsächlichen
    Öffnen nachweislich belegt) gibt es kein automatisches Retry/
    Override mehr — anders als beim früheren, rein heuristischen
    Daemon-Vorab-Check ist der Fehler hier bereits real aufgetreten;
    ein Bestätigungsdialog würde daran nichts ändern. Sinnvoll ist nur
    ein manueller erneuter Versuch, nachdem die Ursache geklärt ist.
    """
    pin = ctx.get_pin()
    with exclusive_session(
        pin,
        lib_path=ctx.pkcs11_lib,
        gateway_host=ctx.gateway_host,
        gateway_port=ctx.gateway_port,
    ) as session:
        yield session


@contextmanager
def cli_read_only_session(ctx: CliContext, need_pin: bool = True):
    pin = ctx.get_pin() if need_pin else None
    with read_only_session(user_pin=pin, lib_path=ctx.pkcs11_lib) as session:
        yield session


def warn_if_gateway_reachable(ctx: CliContext) -> None:
    """Rein informativ, blockiert nichts (siehe
    pico_hsm_tools.pkcs11_session.check_gateway_reachable). Für
    subprocess-basierte Befehle, die keine eigene python-pkcs11-Session
    öffnen und daher den harten Konflikt-Check aus `exclusive_session()`
    nicht automatisch durchlaufen — ob tatsächlich ein Konflikt vorliegt,
    zeigt letztlich der Exit-Code des externen Tools."""
    state = check_gateway_reachable(ctx.gateway_host, ctx.gateway_port)
    if state.reachable:
        ctx.echo(
            f"[INFO] Gateway unter {state.host}:{state.port} erreichbar — "
            "falls diese Operation fehlschlägt, könnte ein belegter "
            "Reader die Ursache sein."
        )
