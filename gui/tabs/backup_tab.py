"""backup_tab.py — Backup-Bereich (Schritt 6f: sechster ausgebauter Tab).

Alle vier CLI-Bereiche (`backup split/restore/drill/list`): Splitten
(age+Shamir), Wiederherstellen, Drill (Selbsttest + echt), Backup-Liste.
Core-Logik aus pico_hsm_tools/backup_core.py + backup_index.py (kein
Core-Umbau nötig — reine Funktionen, keine Sessions, keine PINs).

Getroffene Entscheidungen (Schritt 6f): ein Formular pro Bereich,
Mehrzeilen-Share-Eingabe (ein Share pro Zeile), beide Drill-Arten,
Schwelle/Gesamt-Default 3-von-5. Shares sind Secrets und werden nach
erfolgreichem Restore/Drill aus den Feldern gelöscht (Hygiene wie
PIN-Felder).
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
    CompactSpinBox,
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
from pico_hsm_tools import backup_core as bc
from pico_hsm_tools import backup_index


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


class BackupTab(QWidget):
    """Backup-Tab: Splitten, Wiederherstellen, Drill, Liste."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("backup")
        self._info_bars: list[InfoBar] = []
        self._worker: FunctionWorker | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(TitleLabel("Backup", self))

        # --- Splitten ------------------------------------------------------
        layout.addWidget(StrongBodyLabel("Backup splitten (age + Shamir)", self))
        split_file_row = QHBoxLayout()
        self.splitFileEdit = LineEdit(self)
        self.splitFileEdit.setObjectName("splitFileEdit")
        self.splitFileEdit.setPlaceholderText("Zu sichernde Datei")
        self.splitFileBrowseButton = PushButton("Durchsuchen", self)
        self.splitFileBrowseButton.setObjectName("splitFileBrowseButton")
        self.splitFileBrowseButton.clicked.connect(self._on_split_file_browse)
        split_file_row.addWidget(self.splitFileEdit, 1)
        split_file_row.addWidget(self.splitFileBrowseButton)
        layout.addLayout(split_file_row)
        split_dir_row = QHBoxLayout()
        self.splitDirEdit = LineEdit(self)
        self.splitDirEdit.setObjectName("splitDirEdit")
        self.splitDirEdit.setPlaceholderText("Zielordner für das Backup")
        self.splitDirBrowseButton = PushButton("Durchsuchen", self)
        self.splitDirBrowseButton.setObjectName("splitDirBrowseButton")
        self.splitDirBrowseButton.clicked.connect(self._on_split_dir_browse)
        split_dir_row.addWidget(self.splitDirEdit, 1)
        split_dir_row.addWidget(self.splitDirBrowseButton)
        layout.addLayout(split_dir_row)
        split_param_row = QHBoxLayout()
        self.thresholdSpin = CompactSpinBox(self)
        self.thresholdSpin.setObjectName("thresholdSpin")
        self.thresholdSpin.setRange(1, 255)
        self.thresholdSpin.setValue(3)
        self.thresholdSpin.setPrefix("m ")
        self.totalSpin = CompactSpinBox(self)
        self.totalSpin.setObjectName("totalSpin")
        self.totalSpin.setRange(1, 255)
        self.totalSpin.setValue(5)
        self.identityFileEdit = LineEdit(self)
        self.identityFileEdit.setObjectName("identityFileEdit")
        self.identityFileEdit.setPlaceholderText(
            "age-Identity-Datei (optional, leer = neues Keypair)"
        )
        self.identityBrowseButton = PushButton("Durchsuchen", self)
        self.identityBrowseButton.setObjectName("identityBrowseButton")
        self.identityBrowseButton.clicked.connect(self._on_identity_browse)
        self.splitButton = PrimaryPushButton("Splitten", self)
        self.splitButton.setObjectName("splitButton")
        self.splitButton.clicked.connect(self._on_split)
        split_param_row.addWidget(self.thresholdSpin)
        split_param_row.addWidget(self.totalSpin)
        split_param_row.addWidget(self.identityFileEdit, 1)
        split_param_row.addWidget(self.identityBrowseButton)
        split_param_row.addWidget(self.splitButton)
        layout.addLayout(split_param_row)
        self.splitResultLabel = BodyLabel("", self)
        self.splitResultLabel.setObjectName("splitResultLabel")
        self.splitResultLabel.setWordWrap(True)
        layout.addWidget(self.splitResultLabel)

        # --- Wiederherstellen --------------------------------------------------
        layout.addWidget(StrongBodyLabel("Wiederherstellen", self))
        restore_dir_row = QHBoxLayout()
        self.restoreDirEdit = LineEdit(self)
        self.restoreDirEdit.setObjectName("restoreDirEdit")
        self.restoreDirEdit.setPlaceholderText("Backup-Ordner")
        self.restoreDirBrowseButton = PushButton("Durchsuchen", self)
        self.restoreDirBrowseButton.setObjectName("restoreDirBrowseButton")
        self.restoreDirBrowseButton.clicked.connect(self._on_restore_dir_browse)
        restore_dir_row.addWidget(self.restoreDirEdit, 1)
        restore_dir_row.addWidget(self.restoreDirBrowseButton)
        layout.addLayout(restore_dir_row)
        restore_file_row = QHBoxLayout()
        self.restoreFileEdit = LineEdit(self)
        self.restoreFileEdit.setObjectName("restoreFileEdit")
        self.restoreFileEdit.setPlaceholderText("Ausgabedatei")
        self.restoreFileBrowseButton = PushButton("Durchsuchen", self)
        self.restoreFileBrowseButton.setObjectName("restoreFileBrowseButton")
        self.restoreFileBrowseButton.clicked.connect(self._on_restore_file_browse)
        restore_file_row.addWidget(self.restoreFileEdit, 1)
        restore_file_row.addWidget(self.restoreFileBrowseButton)
        layout.addLayout(restore_file_row)
        self.restoreSharesEdit = TextEdit(self)
        self.restoreSharesEdit.setObjectName("restoreSharesEdit")
        self.restoreSharesEdit.setPlaceholderText(
            "Shares, ein Share pro Zeile (werden nach Erfolg gelöscht)"
        )
        self.restoreSharesEdit.setMaximumHeight(80)
        layout.addWidget(self.restoreSharesEdit)
        self.restoreButton = PrimaryPushButton("Wiederherstellen", self)
        self.restoreButton.setObjectName("restoreButton")
        self.restoreButton.clicked.connect(self._on_restore)
        layout.addWidget(self.restoreButton)

        # --- Drill ---------------------------------------------------------------
        layout.addWidget(StrongBodyLabel("Recovery-Drill", self))
        drill_self_row = QHBoxLayout()
        drill_self_row.addWidget(BodyLabel(
            "Automatisierter Selbsttest ohne echtes Backup.", self,
        ))
        drill_self_row.addStretch(1)
        self.selfTestButton = PrimaryPushButton("Selbsttest", self)
        self.selfTestButton.setObjectName("selfTestButton")
        self.selfTestButton.clicked.connect(self._on_self_test)
        drill_self_row.addWidget(self.selfTestButton)
        layout.addLayout(drill_self_row)
        drill_real_row = QHBoxLayout()
        self.drillDirEdit = LineEdit(self)
        self.drillDirEdit.setObjectName("drillDirEdit")
        self.drillDirEdit.setPlaceholderText("Backup-Ordner für echten Drill")
        self.drillDirBrowseButton = PushButton("Durchsuchen", self)
        self.drillDirBrowseButton.setObjectName("drillDirBrowseButton")
        self.drillDirBrowseButton.clicked.connect(self._on_drill_dir_browse)
        drill_real_row.addWidget(self.drillDirEdit, 1)
        drill_real_row.addWidget(self.drillDirBrowseButton)
        layout.addLayout(drill_real_row)
        self.drillSharesEdit = TextEdit(self)
        self.drillSharesEdit.setObjectName("drillSharesEdit")
        self.drillSharesEdit.setPlaceholderText(
            "Shares, ein Share pro Zeile (werden nach Erfolg gelöscht)"
        )
        self.drillSharesEdit.setMaximumHeight(80)
        layout.addWidget(self.drillSharesEdit)
        self.drillButton = PrimaryPushButton("Echten Drill starten", self)
        self.drillButton.setObjectName("drillButton")
        self.drillButton.clicked.connect(self._on_real_drill)
        layout.addWidget(self.drillButton)

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
        self.backupsTable.setColumnCount(5)
        self.backupsTable.setHorizontalHeaderLabels(
            ["Pfad", "Erstellt", "Schema", "SHA", "Letzter Drill"],
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

    def _on_split_file_browse(self) -> None:
        path = _ask_open_file(self, "Zu sichernde Datei wählen")
        if path:
            self.splitFileEdit.setText(path)

    def _on_split_dir_browse(self) -> None:
        path = _ask_directory(self, "Zielordner wählen")
        if path:
            self.splitDirEdit.setText(path)

    def _on_identity_browse(self) -> None:
        path = _ask_open_file(self, "age-Identity-Datei wählen (optional)")
        if path:
            self.identityFileEdit.setText(path)

    def _on_restore_dir_browse(self) -> None:
        path = _ask_directory(self, "Backup-Ordner wählen")
        if path:
            self.restoreDirEdit.setText(path)

    def _on_restore_file_browse(self) -> None:
        path = _ask_save_file(self, "Ausgabedatei wählen")
        if path:
            self.restoreFileEdit.setText(path)

    def _on_drill_dir_browse(self) -> None:
        path = _ask_directory(self, "Backup-Ordner wählen")
        if path:
            self.drillDirEdit.setText(path)

    def _on_list_dir_browse(self) -> None:
        path = _ask_directory(self, "Eltern-Ordner wählen")
        if path:
            self.listDirEdit.setText(path)

    # --- Splitten --------------------------------------------------------------

    def _on_split(self) -> None:
        export_file = self.splitFileEdit.text().strip()
        out_dir = self.splitDirEdit.text().strip()
        if not export_file or not out_dir:
            self._show_error("Zu sichernde Datei und Zielordner angeben.")
            return
        threshold = self.thresholdSpin.value()
        total = self.totalSpin.value()
        if threshold > total:
            self._show_error(
                f"Schwelle ({threshold}) darf nicht größer als Gesamtzahl "
                f"({total}) sein."
            )
            return
        identity_text = self.identityFileEdit.text().strip()
        identity_file = Path(identity_text) if identity_text else None

        def make() -> FunctionWorker:
            def split():
                return bc.split_backup(
                    Path(export_file), Path(out_dir),
                    threshold, total, identity_file,
                )

            return FunctionWorker(split)

        def done(manifest: dict) -> None:
            self.splitResultLabel.setText(
                f"Public Key: {manifest.get('pubkey', '?')} — "
                "'shares-DO-NOT-KEEP-TOGETHER.txt' nach lokalem Drill "
                "löschen und Einzel-Shares an getrennte Orte verteilen (3-2-1)."
            )
            self._show_success(
                f"Backup erstellt ({threshold}-von-{total})."
            )

        self._start_worker(make, done, self.splitButton)

    # --- Wiederherstellen ----------------------------------------------------------

    def _on_restore(self) -> None:
        backup_dir = self.restoreDirEdit.text().strip()
        output_file = self.restoreFileEdit.text().strip()
        shares = _parse_shares(self.restoreSharesEdit.toPlainText())
        if not backup_dir or not output_file:
            self._show_error("Backup-Ordner und Ausgabedatei angeben.")
            return
        if not shares:
            self._show_error("Mindestens ein Share eingeben.")
            return

        def make() -> FunctionWorker:
            def restore():
                return bc.restore_backup(
                    Path(backup_dir), Path(output_file), shares,
                )

            return FunctionWorker(restore)

        def done(_manifest: dict) -> None:
            self.restoreSharesEdit.setPlainText("")
            self._show_success(f"Wiederhergestellt nach {output_file}.")

        self._start_worker(make, done, self.restoreButton)

    # --- Drill -------------------------------------------------------------------------

    def _on_self_test(self) -> None:
        def make() -> FunctionWorker:
            return FunctionWorker(bc.self_test)

        def done(ok: bool) -> None:
            if ok:
                self._show_success("Selbsttest bestanden.")
            else:
                self._show_error("Selbsttest FEHLGESCHLAGEN.")

        self._start_worker(make, done, self.selfTestButton)

    def _on_real_drill(self) -> None:
        backup_dir = self.drillDirEdit.text().strip()
        shares = _parse_shares(self.drillSharesEdit.toPlainText())
        if not backup_dir:
            self._show_error("Backup-Ordner angeben.")
            return
        if not shares:
            self._show_error("Mindestens ein Share eingeben.")
            return

        def make() -> FunctionWorker:
            def drill():
                return bc.real_drill(Path(backup_dir), shares)

            return FunctionWorker(drill)

        def done(ok: bool) -> None:
            self.drillSharesEdit.setPlainText("")
            if ok:
                self._show_success("Drill bestanden.")
            else:
                self._show_error("Drill FEHLGESCHLAGEN.")

        self._start_worker(make, done, self.drillButton)

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
        leftover = False
        for row, info in enumerate(infos):
            self.backupsTable.setItem(row, 0, QTableWidgetItem(str(info.path)))
            self.backupsTable.setItem(
                row, 1, QTableWidgetItem(info.created_at or "?"),
            )
            self.backupsTable.setItem(
                row, 2,
                QTableWidgetItem(f"{info.threshold}-von-{info.total_shares}"),
            )
            self.backupsTable.setItem(
                row, 3, QTableWidgetItem(info.ciphertext_sha256 or "?"),
            )
            if info.last_drill_at:
                drill_text = f"{info.last_drill_at} -> {info.last_drill_result}"
            else:
                drill_text = "noch nie — Drill empfohlen!"
            self.backupsTable.setItem(row, 4, QTableWidgetItem(drill_text))
            if info.leftover_shares_file_present:
                leftover = True
        if leftover:
            self._show_warning(
                "shares-DO-NOT-KEEP-TOGETHER.txt liegt noch bei einem Backup — "
                "Gesamt-Secret an einem Ort, nach Drill löschen!"
            )
        elif not infos:
            self._show_warning(
                "Keine Backup-Verzeichnisse gefunden (kein manifest.json)."
            )
