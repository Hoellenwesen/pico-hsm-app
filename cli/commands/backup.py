from __future__ import annotations

from pathlib import Path

import click

from pico_hsm_tools import backup_core as bc
from pico_hsm_tools import backup_index
from pico_hsm_tools import hsm_backup as hb

from ..context import CliContext, pass_ctx


@click.group()
def backup() -> None:
    """Backups: HSM-Backup (Token-Inhalt, age-Empfänger-Modus 1-aus-n)
    und Backup-Liste mit Hygiene.

    Kein Shamir/ssss mehr (siehe docs/20-roadmap.md) — als künftige
    Verbesserung ist Python-Shamir geparkt.
    """


@backup.command("list")
@click.argument("parent_dir", type=click.Path(exists=True, path_type=Path))
@pass_ctx
def list_cmd(ctx: CliContext, parent_dir: Path) -> None:
    """Backup-Verzeichnisse unter parent_dir mit Status auflisten."""
    infos = backup_index.list_backups(parent_dir)
    ctx.emit_json({"backups": [i.__dict__ for i in infos]})

    if not infos:
        ctx.echo("Keine Backup-Verzeichnisse gefunden (kein manifest.json).")
        return

    for info in infos:
        ctx.echo(f"\n{info.path}")
        created = info.created_at or "?"
        if info.age_days is not None:
            created = f"{created} (vor {info.age_days} Tagen)"
        ctx.echo(f"  Erstellt:        {created}")
        ctx.echo(f"  Schema:          {backup_index.schema_text(info)}")
        ctx.echo(f"  Ciphertext-SHA:  {info.ciphertext_sha256 or '?'}")
        ctx.echo(f"  Letzter Drill:   {backup_index.drill_text(info)}")
        ctx.echo(f"  Hygiene:         {backup_index.hygiene_label(info)}")
        for warning in backup_index.hygiene_warnings(info):
            ctx.echo(f"  [WARN] {warning}")


def _hsm_parts(keys: bool, data: bool, options: bool, all_parts: bool) -> list[str]:
    """Auswahl auflösen: --all (Default) oder einzelne Checkboxen."""
    if all_parts or not (keys or data or options):
        return list(hb.PARTS)
    selected = []
    if keys:
        selected.append("keys")
    if data:
        selected.append("data")
    if options:
        selected.append("options")
    return selected


def _key_ref_overrides(raw: tuple[str, ...]) -> dict[str, int]:
    """LABEL:REF-Paare parsen (manueller Key-Reference-Override)."""
    overrides: dict[str, int] = {}
    for item in raw:
        if ":" not in item:
            raise ValueError(
                f"Ungültig (erwartet LABEL:REF): {item!r}.")
        label, _, ref = item.partition(":")
        try:
            overrides[label.strip()] = int(ref.strip())
        except ValueError:
            raise ValueError(
                f"Ungültig (REF muss Zahl sein): {item!r}.")
    return overrides


@backup.command("hsm-backup")
@click.argument("out_dir", type=click.Path(path_type=Path))
@click.option("--all", "all_parts", is_flag=True, default=True,
              help="Vollbackup (alle Teile, Default).")
@click.option("--keys/--no-keys", default=True, help="Keys per DKEK-Wrap sichern.")
@click.option("--data/--no-data", default=True, help="Datenobjekte sichern.")
@click.option("--options/--no-options", default=True, help="Dynamic Options sichern.")
@click.option("--recipient", "recipients", multiple=True, required=True,
              help="Empfänger-Pubkey (mehrfach, 1-aus-n).")
@click.option("--key-ref", "key_refs", multiple=True,
              help="Manueller Key-Reference-Override (LABEL:REF, mehrfach).")
@pass_ctx
def hsm_backup_cmd(ctx: CliContext, out_dir: Path, all_parts: bool,
                   keys: bool, data: bool, options: bool,
                   recipients: tuple[str, ...],
                   key_refs: tuple[str, ...]) -> None:
    """Echtes HSM-Backup: Token-Inhalt sichern (nicht nur Dateien).

    Braucht DKEK mit Shares (sonst Abbruch mit Anleitung) und die
    User-PIN (--pin-env oder Prompt). Versiegelung per Empfänger
    (--recipient, mehrfach, 1-aus-n). PINs, DKEK und OTP migrieren nie
    (siehe Doku) — nur Keys, Daten und Optionen landen im Bundle.
    """
    parts = _hsm_parts(keys, data, options, all_parts)
    try:
        overrides = _key_ref_overrides(key_refs)
    except ValueError as exc:
        ctx.fail(str(exc))
        return
    pin = ctx.get_pin()
    try:
        hb.check_dkek_ready()
        inventory = hb.collect_inventory(pin, ctx.serial, ctx.pkcs11_lib)
        staging = out_dir / ".hsm-staging"
        manifest = hb.export_parts(
            inventory, parts, staging, pin, ctx.serial, ctx.pkcs11_lib,
            overrides)
        sealed = hb.seal_bundle(staging, out_dir, list(recipients))
    except hb.HsmBackupError as exc:
        ctx.fail(str(exc), exit_code=2)
        return
    ctx.emit_json({"status": "ok", "parts": parts,
                   "keys": [k["label"] for k in manifest.keys],
                   "pubkey": sealed.get("pubkey")})
    ctx.echo(f"[OK] HSM-Backup nach {out_dir} ({', '.join(parts)}).")
    ctx.echo(f"  Keys: {len(manifest.keys)}, Daten: {len(manifest.data_objects)}.")
    ctx.echo(
        f"  Empfänger-Modus ({len(sealed['recipients'])} Empfänger, "
        "1-aus-n) — Datei an alle Standorte verteilen.")


@backup.command("hsm-restore")
@click.argument("backup_dir", type=click.Path(exists=True, path_type=Path))
@click.option("--identity-file", type=click.Path(exists=True, path_type=Path),
              required=True,
              help="Identity-Datei eines Empfängers.")
@click.option("--work-dir", type=click.Path(path_type=Path), default=None,
              help="Arbeitsverzeichnis (Default: Unterordner im Backup).")
@click.option("--force", "force_flag", is_flag=True,
              help="Unwrap auf belegte References erzwingen.")
@pass_ctx
def hsm_restore_cmd(ctx: CliContext, backup_dir: Path,
                    identity_file: Path,
                    work_dir: Path | None, force_flag: bool) -> None:
    """HSM-Backup auf (neuer) Hardware wiederherstellen.

    Voraussetzung: Token initialisiert + derselbe DKEK per Shares
    importiert (User-PIN/SO-PIN neu vergeben). Danach Unwrap, Daten,
    Optionen + Verifikation gegen Manifest (Key-References stehen im
    Manifest, kein Override nötig).
    """
    pin = ctx.get_pin()
    work = work_dir or (backup_dir / ".hsm-restore-work")
    try:
        bundle_zip = hb.open_sealed_bundle(
            backup_dir, work, identity_file)
        report, manifest_dict = hb.apply_bundle(
            bundle_zip, pin, ctx.serial, ctx.pkcs11_lib,
            force=ctx.force or force_flag)
        verification = hb.verify_against_manifest(
            hb.HsmManifest.from_dict(manifest_dict),
            pin, ctx.serial, ctx.pkcs11_lib)
    except hb.HsmBackupError as exc:
        ctx.fail(str(exc), exit_code=2)
        return
    ctx.emit_json({"status": "ok", "report": report,
                   "verification": verification})
    ctx.echo(f"[OK] Wiederhergestellt: {len(report['keys'])} Keys, "
             f"{len(report['data'])} Datenobjekte.")
    for quirk in report.get("quirks", []):
        ctx.echo(f"  [HINWEIS] {quirk}")
    missing = verification["missing_keys"] + verification["missing_data"]
    if missing:
        ctx.echo(f"  [WARN] Fehlt nach Verifikation: {', '.join(missing)}.")
    if verification["options_ok"] is False:
        ctx.echo("  [WARN] Dynamic Options weichen vom Manifest ab.")
