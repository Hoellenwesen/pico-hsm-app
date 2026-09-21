from __future__ import annotations

import click

from pico_hsm_tools import apdu_core as ac
from pico_hsm_tools import flash_core as fc

from ..context import CliContext, pass_ctx
from ._common import require_bootsel, require_normal


@click.group()
def setup() -> None:
    """OTP-Anzeige und Dynamic Options (teils read-only)."""


def _apdu_connection(ctx: CliContext | None = None) -> ac.CardConnectionLike:
    """APDU-Verbindung öffnen. Fehler -> ApduError (UI-Schicht mappt)."""
    reader = ctx.reader if ctx is not None else None
    return ac.open_connection(reader)


@setup.group("dynamic-options")
def dynopts_grp() -> None:
    """Dynamic Options lesen/schreiben (Vendor-APDU, siehe apdu_core.py).

    VORAUSSETZUNG (Firmware, am Board gemessen): vorheriger PIN-Login,
    sonst antwortet die Karte SW=6982. Für `get`/`set` daher zuerst
    anderweitig einloggen (z.B. `keys list` mit PIN), Session danach
    schließen (sequenziell/exklusiv-Regel §7.b).
    """


def _echo_dynopts(ctx: CliContext, options: ac.DynamicOptions) -> None:
    ctx.emit_json({
        "press_to_confirm": options.press_to_confirm,
        "key_usage_counter": options.key_usage_counter,
    })
    ctx.echo(
        f"Press-to-Confirm:   {'an' if options.press_to_confirm else 'aus'}\n"
        f"Key-Usage-Counter:  {'an' if options.key_usage_counter else 'aus'}"
    )


@dynopts_grp.command("get")
@pass_ctx
@require_normal
def dynopts_get(ctx: CliContext) -> None:
    """Aktuelle Dynamic Options anzeigen."""
    try:
        conn = _apdu_connection(ctx)
        try:
            options = ac.get_dynamic_options(conn)
        finally:
            conn.disconnect()
    except ac.ApduError as exc:
        ctx.fail(str(exc), exit_code=2)
        return
    _echo_dynopts(ctx, options)


@dynopts_grp.command("set")
@click.option(
    "--press-to-confirm/--no-press-to-confirm", default=None,
    help="Tasten-Bestätigung bei privatem/geheimem Key-Gebrauch.",
)
@click.option(
    "--key-usage-counter/--no-key-usage-counter", default=None,
    help="Nutzungszähler für alle Keys (neue Keys starten bei 2^32-1).",
)
@pass_ctx
@require_normal
def dynopts_set(
    ctx: CliContext,
    press_to_confirm: bool | None,
    key_usage_counter: bool | None,
) -> None:
    """Dynamic Options setzen — mindestens eine Option angeben.

    Sicherheitsauflage (Architekturkonzept §9): Das DEAKTIVIEREN von
    Press-to-Confirm braucht eine explizite Bestätigung (außer --force),
    nicht nur das Aktivieren.
    """
    if press_to_confirm is None and key_usage_counter is None:
        ctx.fail(
            "Mindestens eine Option angeben "
            "(--press-to-confirm/--no-press-to-confirm, "
            "--key-usage-counter/--no-key-usage-counter).",
            exit_code=1,
        )
        return
    try:
        conn = _apdu_connection(ctx)
        try:
            current = ac.get_dynamic_options(conn)
        finally:
            conn.disconnect()
    except ac.ApduError as exc:
        ctx.fail(str(exc), exit_code=2)
        return
    target = ac.DynamicOptions(
        press_to_confirm=(
            press_to_confirm
            if press_to_confirm is not None
            else current.press_to_confirm
        ),
        key_usage_counter=(
            key_usage_counter
            if key_usage_counter is not None
            else current.key_usage_counter
        ),
    )
    if current.press_to_confirm and not target.press_to_confirm:
        if not ctx.confirm(
            "Press-to-Confirm DEAKTIVIEREN? Danach bestätigt das Token "
            "private/geheime Key-Operationen ohne Tastendruck."
        ):
            ctx.fail("Abgebrochen — nichts geändert.", exit_code=1)
            return
    try:
        conn = _apdu_connection(ctx)
        try:
            ac.set_dynamic_options(conn, target)
        finally:
            conn.disconnect()
    except ac.ApduError as exc:
        ctx.fail(str(exc), exit_code=2)
        return
    ctx.echo("[OK] Dynamic Options gesetzt:")
    _echo_dynopts(ctx, target)


@setup.command("show")
@pass_ctx
@require_bootsel
def show(ctx: CliContext) -> None:
    """Secure-Boot-Fingerprint, Anti-Rollback, Debug-Lock, Rollback-Zähler.

    Bewusst kein `setup set ...`: OTP-Flags sind One-Way-Schalter
    (Thermometer-Code) mit Brick-Risiko bei Fehlbedienung. Das Setzen
    bleibt dem dokumentierten manuellen Ablauf mit Ersatzboard-Test
    vorbehalten (docs/02-firmware-update-security.md).

    Der Pubkey-Fingerprint nutzt dieselbe Funktion wie
    flash_core.py::get_burned_key_fingerprint() (mehrere BOOTKEY0_N-OTP-
    Wörter zusammengesetzt, hart abgelehnt statt unvollständig
    zurückgegeben — siehe dortige Docstring für Details/offene Punkte).
    """
    payload: dict[str, str] = {}

    try:
        payload["pubkey_fingerprint"] = fc.get_burned_key_fingerprint()
    except fc.FlashError as exc:
        payload["pubkey_fingerprint"] = f"nicht lesbar ({exc})"

    for field, key in fc.OTP_FLAG_FIELDS:
        payload[key] = fc.read_otp_field(field)

    ctx.emit_json(payload)
    for key, value in payload.items():
        ctx.echo(f"{key}: {value}")
