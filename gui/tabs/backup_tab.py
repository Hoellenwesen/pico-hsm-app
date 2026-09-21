"""backup_tab.py — Backup-Bereich.

CLI-Bereiche (`backup list/hsm-backup/hsm-restore`): echtes HSM-Backup
(Token-Inhalt: Keys per DKEK-Wrap, Datenobjekte, Optionen; Vollbackup
oder benutzerdefiniert per Checkbox), versiegelt per Empfänger
(age, 1-aus-n) — plus Backup-Liste mit Hygiene.
Core-Logik aus backup_core.py + backup_index.py + hsm_backup.py (PIN aus
Anmeldung/Vault, keine eigenen PIN-Felder).

Getroffene Entscheidung: ein Formular pro Bereich.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CheckBox,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    TableWidget,
    TextEdit,
    TitleLabel,
)

from gui.workers import FunctionWorker
from pico_hsm_tools import backup_index
from pico_hsm_tools import hsm_backup as hb


def _ask_open_file(parent: QWidget, caption: str) -> str | None:
    """Öffnen-Dialog (eigene Funktion: in Tests ohne echten Dialog)."""
    path, _ = QFileDialog.getOpenFileName(parent, caption)
    return path or None


def _ask_save_file(parent: QWidget, caption: str) -> str | None:
    """Speichern-Dialog (eigene Funktion: in Tests ohne echten Dialog)."""
    path, _ = QFileDialog.getSaveFileName(parent, caption)
    return path or None


def _ask_directory(parent: QWidget, caption: str) -> str | None:
    """Ordner-Dialog (eigene Funktion: in Tests ohne echten Dialog)."""
    path = QFileDialog.getExistingDirectory(parent, caption)
    return path or None


def _parse_shares(text: str) -> list[str]:
    """Share-Strings aus Mehrzeilen-Text (Leerzeilen ignoriert)."""
    return [line.strip() for line in text.splitlines() if line.strip()]


def _do_hsm_backup(parts: list[str], out_dir: str,
                   pin: str, serial: str | None,
                   recipients: list[str]) -> dict:
    """HSM-Backup im Worker: Gate, Inventar, Export, Versiegelung."""
    hb.check_dkek_ready()
    inventory = hb.collect_inventory(pin, serial)
    staging = Path(out_dir) / ".hsm-staging"
    manifest = hb.export_parts(
        inventory, parts, staging, pin, serial)
    sealed = hb.seal_bundle(staging, Path(out_dir), recipients)
    return {
        "parts": parts,
        "keys": [k["label"] for k in manifest.keys],
        "data": [d["label"] for d in manifest.data_objects],
        "pubkey": sealed.get("pubkey"),
        "recipients": sealed.get("recipients", []),
    }


def _do_hsm_restore(backup_dir: str, pin: str,
                    serial: str | None, force: bool,
                    identity_file: str) -> dict:
    """HSM-Restore im Worker: Öffnen, Anwenden, Verifizieren."""
    import json

    work = Path(backup_dir) / ".hsm-restore-work"
    bundle_zip = hb.open_sealed_bundle(
        Path(backup_dir), work, Path(identity_file))
    report, manifest_dict = hb.apply_bundle(
        bundle_zip, pin, serial, force=force)
    manifest = hb.HsmManifest.from_dict(manifest_dict)
    verification = hb.verify_against_manifest(manifest, pin, serial)
    return {"report": report, "verification": verification}


class BackupTab(QWidget):
    """Backup-Tab: HSM-Backup, HSM-Restore, Backup-Liste."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("backup")
        self._info_bars: list[InfoBar] = []
        self._worker: FunctionWorker | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(TitleLabel("Backup", self))

        # --- HSM-Backup (Token-Inhalt) -------------------------------------
        layout.addWidget(StrongBodyLabel("HSM-Backup (Token-Inhalt)", self))
        layout.addWidget(BodyLabel(
            "Sichert Keys (DKEK-Wrap), Datenobjekte und Optionen vom Token "
            "für Restore auf neuer Hardware. Braucht DKEK mit Shares "
            "(sonst Abbruch) und Anmeldung (PIN aus Vault). PINs, DKEK "
            "und OTP migrieren nie.",
            self,
        ))
        self.hsmAllCheck = CheckBox("Vollbackup (alle Teile)", self)
        self.hsmAllCheck.setObjectName("hsmAllCheck")
        self.hsmAllCheck.setChecked(True)
        self.hsmAllCheck.stateChanged.connect(self._on_hsm_all_changed)
        layout.addWidget(self.hsmAllCheck)
        parts_row = QHBoxLayout()
        self.hsmKeysCheck = CheckBox("Keys", self)
        self.hsmKeysCheck.setObjectName("hsmKeysCheck")
        self.hsmKeysCheck.setChecked(True)
        self.hsmDataCheck = CheckBox("Datenobjekte", self)
        self.hsmDataCheck.setObjectName("hsmDataCheck")
        self.hsmDataCheck.setChecked(True)
        self.hsmOptionsCheck = CheckBox("Optionen", self)
        self.hsmOptionsCheck.setObjectName("hsmOptionsCheck")
        self.hsmOptionsCheck.setChecked(True)
        for check in (self.hsmKeysCheck, self.hsmDataCheck,
                      self.hsmOptionsCheck):
            check.stateChanged.connect(self._on_hsm_part_changed)
            parts_row.addWidget(check)
        parts_row.addStretch(1)
        layout.addLayout(parts_row)
        hsm_param_row = QHBoxLayout()
        self.hsmOutEdit = LineEdit(self)
        self.hsmOutEdit.setObjectName("hsmOutEdit")
        self.hsmOutEdit.setPlaceholderText("Zielordner für das HSM-Backup")
        self.hsmOutBrowseButton = PushButton("Durchsuchen", self)
        self.hsmOutBrowseButton.setObjectName("hsmOutBrowseButton")
        self.hsmOutBrowseButton.clicked.connect(self._on_hsm_out_browse)
        self.hsmBackupButton = PrimaryPushButton("HSM-Backup erstellen", self)
        self.hsmBackupButton.setObjectName("hsmBackupButton")
        self.hsmBackupButton.clicked.connect(self._on_hsm_backup)
        hsm_param_row.addWidget(self.hsmOutEdit, 1)
        hsm_param_row.addWidget(self.hsmOutBrowseButton)
        hsm_param_row.addWidget(self.hsmBackupButton)
        layout.addLayout(hsm_param_row)
        self.hsmRecipientsEdit = TextEdit(self)
        self.hsmRecipientsEdit.setObjectName("hsmRecipientsEdit")
        self.hsmRecipientsEdit.setPlaceholderText(
            "Empfänger-Pubkeys, einer pro Zeile (Pflicht — 1-aus-n, "
            "Format age1...; Keypaar: `age-keygen -o identity.txt`)"
        )
        self.hsmRecipientsEdit.setMaximumHeight(60)
        layout.addWidget(self.hsmRecipientsEdit)
        # --- HSM-Restore -------------------------------------------------
        layout.addWidget(StrongBodyLabel("HSM-Restore (neue Hardware)", self))
        layout.addWidget(BodyLabel(
            "Voraussetzung: Token initialisiert + derselbe DKEK per Shares "
            "importiert. Danach Unwrap, Daten, Optionen + Verifikation.",
            self,
        ))
        hsm_restore_row = QHBoxLayout()
        self.hsmRestoreDirEdit = LineEdit(self)
        self.hsmRestoreDirEdit.setObjectName("hsmRestoreDirEdit")
        self.hsmRestoreDirEdit.setPlaceholderText("HSM-Backup-Ordner")
        self.hsmRestoreBrowseButton = PushButton("Durchsuchen", self)
        self.hsmRestoreBrowseButton.setObjectName("hsmRestoreBrowseButton")
        self.hsmRestoreBrowseButton.clicked.connect(
            self._on_hsm_restore_browse)
        self.hsmForceCheck = CheckBox("Belegte References überschreiben", self)
        self.hsmForceCheck.setObjectName("hsmForceCheck")
        hsm_restore_row.addWidget(self.hsmRestoreDirEdit, 1)
        hsm_restore_row.addWidget(self.hsmRestoreBrowseButton)
        hsm_restore_row.addWidget(self.hsmForceCheck)
        layout.addLayout(hsm_restore_row)
        hsm_restore_identity_row = QHBoxLayout()
        self.hsmRestoreIdentityEdit = LineEdit(self)
        self.hsmRestoreIdentityEdit.setObjectName("hsmRestoreIdentityEdit")
        self.hsmRestoreIdentityEdit.setPlaceholderText(
            "Identity-Datei eines Empfängers (Pflicht)"
        )
        self.hsmRestoreIdentityBrowseButton = PushButton("Durchsuchen", self)
        self.hsmRestoreIdentityBrowseButton.setObjectName(
            "hsmRestoreIdentityBrowseButton")
        self.hsmRestoreIdentityBrowseButton.clicked.connect(
            self._on_hsm_restore_identity_browse)
        hsm_restore_identity_row.addWidget(self.hsmRestoreIdentityEdit, 1)
        hsm_restore_identity_row.addWidget(self.hsmRestoreIdentityBrowseButton)
        layout.addLayout(hsm_restore_identity_row)
        self.hsmRestoreButton = PrimaryPushButton("HSM-Restore starten", self)
        self.hsmRestoreButton.setObjectName("hsmRestoreButton")
        self.hsmRestoreButton.clicked.connect(self._on_hsm_restore)
        layout.addWidget(self.hsmRestoreButton)
        self.hsmResultLabel = BodyLabel("", self)
        self.hsmResultLabel.setObjectName("hsmResultLabel")
        self.hsmResultLabel.setWordWrap(True)
        layout.addWidget(self.hsmResultLabel)

        # --- Liste -----------------------------------------------------------------
        list_head = QHBoxLayout()
        list_head.addWidget(StrongBodyLabel("Backups", self))
        list_head.addStretch(1)
        self.backupRefreshButton = PushButton("Aktualisieren", self)
        self.backupRefreshButton.setObjectName("backupRefreshButton")
        self.backupRefreshButton.clicked.connect(self.refresh)
        list_head.addWidget(self.backupRefreshButton)
        layout.addLayout(list_head)
        list_dir_row = QHBoxLayout()
        self.listDirEdit = LineEdit(self)
        self.listDirEdit.setObjectName("listDirEdit")
        self.listDirEdit.setPlaceholderText("Eltern-Ordner mit Backups")
        self.listDirBrowseButton = PushButton("Durchsuchen", self)
        self.listDirBrowseButton.setObjectName("listDirBrowseButton")
        self.listDirBrowseButton.clicked.connect(self._on_list_dir_browse)
        list_dir_row.addWidget(self.listDirEdit, 1)
        list_dir_row.addWidget(self.listDirBrowseButton)
        layout.addLayout(list_dir_row)
        self.backupsTable = TableWidget(self)
        self.backupsTable.setObjectName("backupsTable")
        self.backupsTable.setColumnCount(6)
        self.backupsTable.setHorizontalHeaderLabels(
            ["Pfad", "Erstellt", "Schema", "SHA", "Letzter Drill", "Hygiene"],
        )
        self.backupsTable.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.backupsTable)

        layout.addStretch(0)

    # --- Helfer ------------------------------------------------------------

    def _show_error(self, text: str) -> None:
        bar = InfoBar.error(
            "Fehler", text, parent=self,
            position=InfoBarPosition.TOP, duration=-1,
        )
        self._info_bars.append(bar)

    def _show_success(self, text: str) -> None:
        bar = InfoBar.success(
            "Erfolg", text, parent=self,
            position=InfoBarPosition.TOP, duration=8000,
        )
        self._info_bars.append(bar)

    def _show_warning(self, text: str) -> None:
        bar = InfoBar.warning(
            "Hinweis", text, parent=self,
            position=InfoBarPosition.TOP, duration=-1,
        )
        self._info_bars.append(bar)

    def _start_worker(
        self,
        make_worker: Callable[[], FunctionWorker],
        on_finished: Callable[[Any], None],
        button: PushButton,
    ) -> None:
        """Worker starten (Button-Sperre, Fehler als InfoBar)."""
        button.setEnabled(False)
        worker = make_worker()

        def _finished(result: Any) -> None:
            button.setEnabled(True)
            self._worker = None
            on_finished(result)

        def _failed(exc: Exception) -> None:
            button.setEnabled(True)
            self._worker = None
            self._show_error(f"Fehlgeschlagen ({exc}).")

        worker.signals.finished.connect(_finished)
        worker.signals.error.connect(_failed)
        self._worker = worker
        QThreadPool.globalInstance().start(worker)

    # --- Dateien -------------------------------------------------------------







    def _on_list_dir_browse(self) -> None:
        path = _ask_directory(self, "Eltern-Ordner wählen")
        if path:
            self.listDirEdit.setText(path)

    # --- HSM-Backup ------------------------------------------------------------

    def _vault_pin(self) -> str | None:
        from gui.pin_vault import vault

        pin = vault.get()
        if not pin:
            self._show_error("Gesperrt — bitte zuerst anmelden (Start-Tab).")
            return None
        return pin

    def _selected_serial(self) -> str | None:
        from gui.session_helpers import selected_serial

        return selected_serial()

    def _hsm_parts(self) -> list[str]:
        parts = []
        if self.hsmKeysCheck.isChecked():
            parts.append("keys")
        if self.hsmDataCheck.isChecked():
            parts.append("data")
        if self.hsmOptionsCheck.isChecked():
            parts.append("options")
        return parts

    def _on_hsm_all_changed(self) -> None:
        checked = self.hsmAllCheck.isChecked()
        for check in (self.hsmKeysCheck, self.hsmDataCheck,
                      self.hsmOptionsCheck):
            check.blockSignals(True)
            check.setChecked(checked)
            check.blockSignals(False)

    def _on_hsm_part_changed(self) -> None:
        all_checked = all((
            self.hsmKeysCheck.isChecked(), self.hsmDataCheck.isChecked(),
            self.hsmOptionsCheck.isChecked(),
        ))
        self.hsmAllCheck.blockSignals(True)
        self.hsmAllCheck.setChecked(all_checked)
        self.hsmAllCheck.blockSignals(False)

    def _on_hsm_out_browse(self) -> None:
        path = _ask_directory(self, "Zielordner für das HSM-Backup wählen")
        if path:
            self.hsmOutEdit.setText(path)

    def _on_hsm_restore_browse(self) -> None:
        path = _ask_directory(self, "HSM-Backup-Ordner wählen")
        if path:
            self.hsmRestoreDirEdit.setText(path)

    def _on_hsm_backup(self) -> None:
        parts = self._hsm_parts()
        if not parts:
            self._show_error("Mindestens einen Teil wählen (oder Vollbackup).")
            return
        out_dir = self.hsmOutEdit.text().strip()
        if not out_dir:
            self._show_error("Zielordner angeben.")
            return
        recipients = _parse_shares(self.hsmRecipientsEdit.toPlainText())
        if not recipients:
            self._show_error(
                "Empfänger-Pubkeys angeben (einer pro Zeile, 1-aus-n).")
            return
        pin = self._vault_pin()
        if not pin:
            return

        def make() -> FunctionWorker:
            return FunctionWorker(
                _do_hsm_backup, parts, out_dir, pin,
                self._selected_serial(), recipients,
            )

        def done(result: dict) -> None:
            keys = ", ".join(result["keys"]) or "—"
            data = ", ".join(result["data"]) or "—"
            self.hsmResultLabel.setText(
                f"HSM-Backup ok ({', '.join(result['parts'])}, "
                f"{len(recipients)} Empfänger, 1-aus-n). "
                f"Keys: {keys}. Daten: {data}. Datei an alle "
                "Standorte verteilen!"
            )
            self._show_success("HSM-Backup erstellt und versiegelt.")

        self._start_worker(make, done, self.hsmBackupButton)

    def _on_hsm_restore(self) -> None:
        backup_dir = self.hsmRestoreDirEdit.text().strip()
        identity_text = self.hsmRestoreIdentityEdit.text().strip()
        if not backup_dir or not identity_text:
            self._show_error(
                "Backup-Ordner und Identity-Datei angeben.")
            return
        pin = self._vault_pin()
        if not pin:
            return
        force = self.hsmForceCheck.isChecked()

        def make() -> FunctionWorker:
            return FunctionWorker(
                _do_hsm_restore, backup_dir, pin,
                self._selected_serial(), force, identity_text,
            )

        def done(result: dict) -> None:
            report, verification = result["report"], result["verification"]
            missing = (verification["missing_keys"]
                       + verification["missing_data"])
            text = (
                f"Restore ok: {len(report['keys'])} Keys, "
                f"{len(report['data'])} Datenobjekte."
            )
            if missing:
                text += f" Fehlt: {', '.join(missing)}."
                self._show_warning(text)
            else:
                self._show_success(text)
            if verification["options_ok"] is False:
                self._show_warning("Dynamic Options weichen vom Manifest ab.")
            self.hsmResultLabel.setText(text)

        self._start_worker(make, done, self.hsmRestoreButton)



    def _on_hsm_restore_identity_browse(self) -> None:
        path = _ask_open_file(self, "Identity-Datei wählen")
        if path:
            self.hsmRestoreIdentityEdit.setText(path)

    # --- Liste ---------------------------------------------------------------------------

    def refresh(self) -> None:
        """Backup-Liste neu laden (Button + Tab-Wechsel)."""
        parent_dir = self.listDirEdit.text().strip()
        if not parent_dir:
            self._show_error("Eltern-Ordner angeben.")
            return

        def make() -> FunctionWorker:
            def list_backups():
                return backup_index.list_backups(Path(parent_dir))

            return FunctionWorker(list_backups)

        self._start_worker(make, self._on_list_finished, self.backupRefreshButton)

    def _on_list_finished(self, infos: list) -> None:
        self.backupsTable.setRowCount(len(infos))
        warn_count = 0
        for row, info in enumerate(infos):
            self.backupsTable.setItem(row, 0, QTableWidgetItem(str(info.path)))
            created = info.created_at or "?"
            if getattr(info, "age_days", None) is not None:
                created = f"{created} (vor {info.age_days} Tagen)"
            self.backupsTable.setItem(row, 1, QTableWidgetItem(created))
            self.backupsTable.setItem(
                row, 2,
                QTableWidgetItem(backup_index.schema_text(info)),
            )
            self.backupsTable.setItem(
                row, 3, QTableWidgetItem(info.ciphertext_sha256 or "?"),
            )
            drill_text = backup_index.drill_text(info)
            self.backupsTable.setItem(row, 4, QTableWidgetItem(drill_text))
            label = backup_index.hygiene_label(info)
            self.backupsTable.setItem(row, 5, QTableWidgetItem(label))
            if label != "OK":
                warn_count += 1
        if warn_count:
            self._show_warning(
                f"Hygiene: {warn_count}/{len(infos)} Backup(s) mit Warnung — "
                "Spalte Hygiene + Letzter Drill prüfen."
            )
        leftover = any(
            getattr(info, "leftover_shares_file_present", False)
            for info in infos
        )
        if leftover:
            self._show_warning(
                "shares-DO-NOT-KEEP-TOGETHER.txt liegt noch bei einem Backup — "
                "Gesamt-Secret an einem Ort, nach Drill löschen!"
            )
        elif not infos:
            self._show_warning(
                "Keine Backup-Verzeichnisse gefunden (kein manifest.json)."
            )
