from __future__ import annotations

from pathlib import Path

import click

from pico_hsm_tools import backup_core as bc
from pico_hsm_tools import backup_index

from ..context import CliContext, pass_ctx


@click.group()
def backup() -> None:
    """Backup/Restore via Shamir Secret Sharing (ssss) + age.

    Komplett in Python (kein Bash mehr) — age und ssss bleiben externe
    Programme, nur die Orchestrierung ist neu. Der age-Identity-String
    wird für den Shamir-Split über Bech32-Dekodierung auf die rohen
    32 Byte reduziert (ssss-Limit: 64 Byte) und nach Rekonstruktion
    wieder zu einem gültigen age-Identity-String zusammengesetzt.
    """


@backup.command("split")
@click.argument("export_file", type=click.Path(exists=True, path_type=Path))
@click.argument("out_dir", type=click.Path(path_type=Path))
@click.option("--threshold", "-m", required=True, type=int, help="Schwelle (m).")
@click.option("--total", "-n", required=True, type=int, help="Anzahl Shares (n).")
@click.option(
    "--identity-file", type=click.Path(exists=True, path_type=Path), default=None,
    help="Vorhandenes age-Identity-File nutzen, statt ein neues zu erzeugen.",
)
@pass_ctx
def split_cmd(
    ctx: CliContext, export_file: Path, out_dir: Path,
    threshold: int, total: int, identity_file: Path | None,
) -> None:
    """Datei verschlüsseln (age) und Identity-Key per Shamir splitten.

    Ohne --identity-file wird ein neues age-Keypair erzeugt (Identity
    existiert danach nur noch gesplittet in den Shares). Mit
    --identity-file wird ein vorhandenes Keypair verwendet und dessen
    Datei unverändert gelassen.
    """
    try:
        manifest = bc.split_backup(
            export_file, out_dir, threshold, total, identity_file,
        )
    except bc.BackupError as exc:
        ctx.fail(str(exc), exit_code=2)
        return

    ctx.emit_json(manifest)
    ctx.echo(f"✓ Backup nach {out_dir} erstellt ({threshold}-von-{total}).")
    ctx.echo(f"  Public Key: {manifest['pubkey']}")
    ctx.echo(
        "  Wichtig: 'shares-DO-NOT-KEEP-TOGETHER.txt' nach lokalem Drill "
        "löschen und Einzel-Shares aus individual-shares/ an getrennte "
        "Orte verteilen (3-2-1)."
    )


@backup.command("restore")
@click.argument("backup_dir", type=click.Path(exists=True, path_type=Path))
@click.argument("output_file", type=click.Path(path_type=Path))
@click.option(
    "--share", "shares", multiple=True,
    help="Ein Share-String (share_id-hexdata). Mehrfach angeben für mehrere Shares.",
)
@pass_ctx
def restore_cmd(
    ctx: CliContext, backup_dir: Path, output_file: Path, shares: tuple[str, ...],
) -> None:
    """Aus Shares rekonstruieren und entschlüsseln.

    Shares entweder per --share (mehrfach) übergeben, oder ohne diese
    Option interaktiv abfragen (leere Zeile beendet die Eingabe) — so
    bleibt der Ablauf nah am realen Custodian-Workflow (Shares aus
    getrennten Lagerorten zusammentragen).
    """
    share_list = list(shares)
    if not share_list:
        ctx.echo("Shares eingeben (leere Zeile zum Abschließen):")
        while True:
            line = click.prompt("Share", default="", show_default=False)
            if not line:
                break
            share_list.append(line.strip())

    try:
        manifest = bc.restore_backup(backup_dir, output_file, share_list)
    except bc.BackupError as exc:
        ctx.fail(str(exc), exit_code=2)
        return

    ctx.emit_json({"status": "ok", "output_file": str(output_file)})
    ctx.echo(f"✓ Wiederhergestellt nach {output_file}.")


@backup.command("drill")
@click.argument("backup_dir", type=click.Path(exists=True, path_type=Path), required=False)
@click.option("--self-test", is_flag=True, help="Automatisierter Selbsttest ohne echtes Backup.")
@click.option("--share", "shares", multiple=True, help="Share für den echten Drill (mehrfach).")
@pass_ctx
def drill_cmd(
    ctx: CliContext, backup_dir: Path | None, self_test: bool, shares: tuple[str, ...],
) -> None:
    """Recovery-Drill: --self-test (automatisiert) oder gegen ein
    Backup-Verzeichnis (Shares per --share oder interaktiv)."""
    if self_test:
        try:
            ok = bc.self_test()
        except Exception as exc:  # noqa: BLE001
            ctx.fail(f"Selbsttest fehlgeschlagen: {exc}", exit_code=1)
            return
        ctx.emit_json({"result": "PASS" if ok else "FAIL"})
        ctx.echo("✓ Selbsttest bestanden." if ok else "✗ Selbsttest fehlgeschlagen.")
        if not ok:
            ctx.fail("Selbsttest fehlgeschlagen.", exit_code=1)
        return

    if not backup_dir:
        ctx.fail("Backup-Verzeichnis erforderlich, außer bei --self-test.")
        return

    share_list = list(shares)
    if not share_list:
        ctx.echo("Shares eingeben (leere Zeile zum Abschließen):")
        while True:
            line = click.prompt("Share", default="", show_default=False)
            if not line:
                break
            share_list.append(line.strip())

    try:
        bc.real_drill(backup_dir, share_list)
    except bc.BackupError as exc:
        ctx.fail(f"Drill fehlgeschlagen: {exc}", exit_code=1)
        return

    ctx.emit_json({"result": "PASS"})
    ctx.echo("✓ Drill bestanden, protokolliert in ~/.pico_hsm/recovery_drills.jsonl")


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
        ctx.echo(f"  Erstellt:        {info.created_at or '?'}")
        ctx.echo(f"  Schema:          {info.threshold}-von-{info.total_shares}")
        ctx.echo(f"  Ciphertext-SHA:  {info.ciphertext_sha256 or '?'}")
        if info.leftover_shares_file_present:
            ctx.echo(
                "  ⚠ shares-DO-NOT-KEEP-TOGETHER.txt liegt noch hier — "
                "Gesamt-Secret an einem Ort, nach Drill löschen!"
            )
        if info.last_drill_at:
            ctx.echo(f"  Letzter Drill:   {info.last_drill_at} → {info.last_drill_result}")
        else:
            ctx.echo("  Letzter Drill:   noch nie getestet — Drill empfohlen!")
