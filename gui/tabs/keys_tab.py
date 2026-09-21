"""keys_tab.py — Schlüssel-Bereich.

Alle acht CLI-Bereiche (`keys list/delete/import/generate/
generate-aes/write-object/read-object/random`): Objektliste mit
Löschen, Import-Hinweis (+ Button zum DKEK-Tab), RSA/EC- und
AES-Erzeugung, Datenobjekte schreiben/lesen, Zufallszahlen.
Core-Logik aus pico_hsm_tools/objects_core.py, Sessions über
gui/session_helpers.py.

Getroffene Entscheidungen: PIN aus dem Vault (Anmeldung, kein Feld
mehr), Inline-Sektionen, Lese-Hex-Vorschau + Speichern-Button.
RSA-2048/4096-Warnung erscheint VORAB bei Auswahl (§7.a-Pflicht),
Erzeugung läuft mit Fortschrittsanzeige im Worker.
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
    ComboBox,
    CompactSpinBox,
    IndeterminateProgressBar,
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

from gui.session_helpers import (
    confirm_destructive,
    open_exclusive_session,
    open_read_only_session,
    show_conflict,
)
from gui.workers import FunctionWorker
from pico_hsm_tools import objects_core as oc
from pico_hsm_tools.pkcs11_session import SessionConflictError


def _ask_open_path(parent: QWidget, caption: str) -> str | None:
    """Öffnen-Dialog (eigene Funktion: in Tests ohne echten Dialog)."""
    path, _ = QFileDialog.getOpenFileName(parent, caption)
    return path or None


def _ask_save_path(parent: QWidget, caption: str) -> str | None:
    """Speichern-Dialog (eigene Funktion: in Tests ohne echten Dialog)."""
    path, _ = QFileDialog.getSaveFileName(parent, caption)
    return path or None


class KeysTab(QWidget):
    """Schlüssel-Tab: Liste, Löschen, Import-Hinweis, Erzeugen (RSA/EC/AES),
    Datenobjekte schreiben/lesen, Zufallszahlen."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("keys")
        self._info_bars: list[InfoBar] = []
        self._worker: FunctionWorker | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(TitleLabel("Schlüssel", self))

        # --- PIN (aus Anmeldung, kein Feld mehr) ---------------------------
        self.pinStateLabel = BodyLabel("", self)
        self.pinStateLabel.setObjectName("pinStateLabel")
        self.pinStateLabel.setWordWrap(True)
        layout.addWidget(self.pinStateLabel)

        # --- Objektliste ---------------------------------------------------
        list_head = QHBoxLayout()
        list_head.addWidget(StrongBodyLabel("Objekte", self))
        list_head.addStretch(1)
        self.keysRefreshButton = PushButton("Aktualisieren", self)
        self.keysRefreshButton.setObjectName("keysRefreshButton")
        self.keysRefreshButton.clicked.connect(self.refresh)
        list_head.addWidget(self.keysRefreshButton)
        layout.addLayout(list_head)
        self.objectsTable = TableWidget(self)
        self.objectsTable.setObjectName("objectsTable")
        self.objectsTable.setColumnCount(4)
        self.objectsTable.setHorizontalHeaderLabels(
            ["Label", "ID", "Klasse", "Typ"],
        )
        self.objectsTable.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.objectsTable)
        self.deleteButton = PrimaryPushButton("Ausgewähltes löschen", self)
        self.deleteButton.setObjectName("deleteButton")
        self.deleteButton.clicked.connect(self._on_delete)
        layout.addWidget(self.deleteButton)

        # --- Import-Hinweis --------------------------------------------------
        import_row = QHBoxLayout()
        import_row.addWidget(BodyLabel(
            "Import nur DKEK-verschlüsselt (kein Roh-Import).", self,
        ))
        import_row.addStretch(1)
        self.gotoDkekButton = PushButton("Zum DKEK-Tab", self)
        self.gotoDkekButton.setObjectName("gotoDkekButton")
        self.gotoDkekButton.clicked.connect(self._on_goto_dkek)
        import_row.addWidget(self.gotoDkekButton)
        layout.addLayout(import_row)

        # --- Erzeugen ---------------------------------------------------------
        layout.addWidget(StrongBodyLabel("Schlüsselpaar erzeugen", self))
        gen_row = QHBoxLayout()
        self.genTypeCombo = ComboBox(self)
        self.genTypeCombo.setObjectName("genTypeCombo")
        self.genTypeCombo.addItems(["rsa", "ec"])
        self.genBitsCombo = ComboBox(self)
        self.genBitsCombo.setObjectName("genBitsCombo")
        self.genBitsCombo.addItems([str(b) for b in oc.RSA_KEY_LENGTHS_BITS])
        self.genBitsCombo.setCurrentText("2048")
        self.genCurveCombo = ComboBox(self)
        self.genCurveCombo.setObjectName("genCurveCombo")
        self.genCurveCombo.addItems(sorted(oc.EC_CURVES))
        self.genIdEdit = LineEdit(self)
        self.genIdEdit.setObjectName("genIdEdit")
        self.genIdEdit.setPlaceholderText("ID (Hex, z.B. 01)")
        self.genLabelEdit = LineEdit(self)
        self.genLabelEdit.setObjectName("genLabelEdit")
        self.genLabelEdit.setPlaceholderText("Label")
        self.generateButton = PrimaryPushButton("Erzeugen", self)
        self.generateButton.setObjectName("generateButton")
        self.generateButton.clicked.connect(self._on_generate)
        gen_row.addWidget(self.genTypeCombo)
        gen_row.addWidget(self.genBitsCombo)
        gen_row.addWidget(self.genCurveCombo)
        gen_row.addWidget(self.genIdEdit)
        gen_row.addWidget(self.genLabelEdit)
        gen_row.addWidget(self.generateButton)
        layout.addLayout(gen_row)
        self.genTypeCombo.currentTextChanged.connect(self._update_generate_ui)
        self.genBitsCombo.currentTextChanged.connect(self._update_generate_ui)
        self.slowWarningLabel = BodyLabel("", self)
        self.slowWarningLabel.setObjectName("slowWarningLabel")
        layout.addWidget(self.slowWarningLabel)
        self.generateProgress = IndeterminateProgressBar(self)
        self.generateProgress.setObjectName("generateProgress")
        self.generateProgress.setVisible(False)
        layout.addWidget(self.generateProgress)
        self._update_generate_ui()

        # --- AES ---------------------------------------------------------------
        layout.addWidget(StrongBodyLabel("AES-Key erzeugen", self))
        aes_row = QHBoxLayout()
        self.aesBitsCombo = ComboBox(self)
        self.aesBitsCombo.setObjectName("aesBitsCombo")
        self.aesBitsCombo.addItems(["128", "192", "256"])
        self.aesBitsCombo.setCurrentText("256")
        self.aesIdEdit = LineEdit(self)
        self.aesIdEdit.setObjectName("aesIdEdit")
        self.aesIdEdit.setPlaceholderText("ID (Hex)")
        self.aesLabelEdit = LineEdit(self)
        self.aesLabelEdit.setObjectName("aesLabelEdit")
        self.aesLabelEdit.setPlaceholderText("Label")
        self.aesButton = PrimaryPushButton("Erzeugen", self)
        self.aesButton.setObjectName("aesButton")
        self.aesButton.clicked.connect(self._on_generate_aes)
        aes_row.addWidget(self.aesBitsCombo)
        aes_row.addWidget(self.aesIdEdit)
        aes_row.addWidget(self.aesLabelEdit)
        aes_row.addWidget(self.aesButton)
        layout.addLayout(aes_row)

        # --- Schreiben -----------------------------------------------------------
        layout.addWidget(StrongBodyLabel("Datenobjekt schreiben", self))
        write_file_row = QHBoxLayout()
        self.writeFileEdit = LineEdit(self)
        self.writeFileEdit.setObjectName("writeFileEdit")
        self.writeFileEdit.setPlaceholderText("Datei (z.B. Zertifikat DER, max. 4096 Byte)")
        self.writeBrowseButton = PushButton("Durchsuchen", self)
        self.writeBrowseButton.setObjectName("writeBrowseButton")
        self.writeBrowseButton.clicked.connect(self._on_write_browse)
        write_file_row.addWidget(self.writeFileEdit, 1)
        write_file_row.addWidget(self.writeBrowseButton)
        layout.addLayout(write_file_row)
        write_param_row = QHBoxLayout()
        self.writeLabelEdit = LineEdit(self)
        self.writeLabelEdit.setObjectName("writeLabelEdit")
        self.writeLabelEdit.setPlaceholderText("Label")
        self.writeIdEdit = LineEdit(self)
        self.writeIdEdit.setObjectName("writeIdEdit")
        self.writeIdEdit.setPlaceholderText("ID (Hex, optional)")
        self.privateCheck = CheckBox("PIN-geschützt", self)
        self.privateCheck.setObjectName("privateCheck")
        self.privateCheck.setChecked(True)
        self.writeButton = PrimaryPushButton("Schreiben", self)
        self.writeButton.setObjectName("writeButton")
        self.writeButton.clicked.connect(self._on_write)
        write_param_row.addWidget(self.writeLabelEdit)
        write_param_row.addWidget(self.writeIdEdit)
        write_param_row.addWidget(self.privateCheck)
        write_param_row.addWidget(self.writeButton)
        layout.addLayout(write_param_row)

        # --- Lesen ---------------------------------------------------------------
        layout.addWidget(StrongBodyLabel("Datenobjekt lesen", self))
        read_row = QHBoxLayout()
        self.readLabelEdit = LineEdit(self)
        self.readLabelEdit.setObjectName("readLabelEdit")
        self.readLabelEdit.setPlaceholderText("Label")
        self.readButton = PrimaryPushButton("Lesen", self)
        self.readButton.setObjectName("readButton")
        self.readButton.clicked.connect(self._on_read)
        self.readSaveButton = PushButton("Speichern unter", self)
        self.readSaveButton.setObjectName("readSaveButton")
        self.readSaveButton.clicked.connect(self._on_read_save)
        read_row.addWidget(self.readLabelEdit)
        read_row.addWidget(self.readButton)
        read_row.addWidget(self.readSaveButton)
        layout.addLayout(read_row)
        self.readPreview = TextEdit(self)
        self.readPreview.setObjectName("readPreview")
        self.readPreview.setReadOnly(True)
        self.readPreview.setPlaceholderText("Hex-Vorschau")
        self.readPreview.setMaximumHeight(100)
        layout.addWidget(self.readPreview)
        self._last_read_bytes: bytes | None = None

        # --- Zufall ----------------------------------------------------------------
        layout.addWidget(StrongBodyLabel("Zufallszahlen", self))
        random_row = QHBoxLayout()
        self.randomSpin = CompactSpinBox(self)
        self.randomSpin.setObjectName("randomSpin")
        self.randomSpin.setRange(1, oc.MAX_RANDOM_BYTES)
        self.randomSpin.setValue(32)
        self.randomButton = PrimaryPushButton("Erzeugen", self)
        self.randomButton.setObjectName("randomButton")
        self.randomButton.clicked.connect(self._on_random)
        random_row.addWidget(self.randomSpin)
        random_row.addWidget(self.randomButton)
        random_row.addStretch(1)
        layout.addLayout(random_row)
        self.randomHex = TextEdit(self)
        self.randomHex.setObjectName("randomHex")
        self.randomHex.setReadOnly(True)
        self.randomHex.setPlaceholderText("Hex-Ausgabe")
        self.randomHex.setMaximumHeight(70)
        layout.addWidget(self.randomHex)

        layout.addStretch(0)

    def hideEvent(self, event) -> None:  # noqa: N802 (Qt-Konvention)
        """Beim Verlassen Vault-Hinweis auffrischen (PIN bleibt im Vault)."""
        self._update_pin_state()
        super().hideEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802 (Qt-Konvention)
        """Beim Betreten Vault-Hinweis auffrischen."""
        super().showEvent(event)
        self._update_pin_state()

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
            position=InfoBarPosition.TOP, duration=5000,
        )
        self._info_bars.append(bar)

    def _require_pin(self) -> str | None:
        from gui.pin_vault import vault

        pin = vault.get()
        if not pin:
            self._show_error(
                "Gesperrt — bitte zuerst anmelden (Start-Tab)."
            )
            return None
        return pin

    def _update_pin_state(self) -> None:
        """Vault-Hinweis aktualisieren (bei Tab-Wechsel + nach Aktionen)."""
        from gui.pin_vault import vault

        if vault.is_unlocked:
            self.pinStateLabel.setText("Entsperrt — PIN aus Anmeldung aktiv.")
        else:
            self.pinStateLabel.setText(
                "Gesperrt — bitte anmelden (Start-Tab), sonst sind alle "
                "Vorgänge blockiert."
            )

    @staticmethod
    def _parse_id(text: str) -> bytes | None:
        try:
            return bytes.fromhex(text.strip())
        except ValueError:
            return None

    def _start_worker(
        self,
        make_worker: Callable[[], FunctionWorker],
        on_finished: Callable[[Any], None],
        button: PushButton,
        on_finally: Callable[[], None] | None = None,
    ) -> None:
        """Worker starten: Button-Sperre, Konflikt-Retry auf Nutzerwunsch
        (kein Auto-Loop), `on_finally` läuft in beiden Pfaden (z.B.
        Fortschrittsanzeige ausblenden)."""
        button.setEnabled(False)
        worker = make_worker()

        def _finished(result: Any) -> None:
            button.setEnabled(True)
            self._worker = None
            if on_finally is not None:
                on_finally()
            on_finished(result)

        def _failed(exc: Exception) -> None:
            if on_finally is not None:
                on_finally()
            self._handle_op_error(exc, make_worker, on_finished, button)

        worker.signals.finished.connect(_finished)
        worker.signals.error.connect(_failed)
        self._worker = worker
        QThreadPool.globalInstance().start(worker)

    def _handle_op_error(
        self,
        exc: Exception,
        make_worker: Callable[[], FunctionWorker],
        on_finished: Callable[[Any], None],
        button: PushButton,
    ) -> None:
        button.setEnabled(True)
        self._worker = None
        if isinstance(exc, SessionConflictError):
            if show_conflict(self, str(exc), allow_retry=True):
                self._start_worker(make_worker, on_finished, button)
                return
            self._show_error(f"Abgebrochen: {exc}")
            return
        self._show_error(f"Fehlgeschlagen ({exc}).")

    def _on_goto_dkek(self) -> None:
        show_tab = getattr(self.window(), "show_tab", None)
        if callable(show_tab):
            show_tab("dkek")

    def _update_generate_ui(self, *_args: object) -> None:
        is_rsa = self.genTypeCombo.currentText() == "rsa"
        self.genBitsCombo.setVisible(is_rsa)
        self.genCurveCombo.setVisible(not is_rsa)
        warning = ""
        if is_rsa:
            try:
                bits = int(self.genBitsCombo.currentText())
            except ValueError:
                bits = 0
            warning = oc.RSA_SLOW_WARNING_BITS.get(bits, "")
        self.slowWarningLabel.setText(
            f"[INFO] RSA-Erzeugung {warning} — bitte warten." if warning else ""
        )

    # --- Liste/Löschen -------------------------------------------------------

    def refresh(self) -> None:
        """Objektliste neu laden (Button + Tab-Wechsel)."""
        pin = self._require_pin()
        if pin is None:
            return

        def make() -> FunctionWorker:
            def load():
                with open_read_only_session(pin) as session:
                    return oc.list_objects(session)

            return FunctionWorker(load)

        self._start_worker(make, self._on_list_finished, self.keysRefreshButton)

    def _on_list_finished(self, objects: list) -> None:
        self.objectsTable.setRowCount(len(objects))
        for row, info in enumerate(objects):
            self.objectsTable.setItem(row, 0, QTableWidgetItem(info.label or ""))
            self.objectsTable.setItem(
                row, 1, QTableWidgetItem(info.id.hex() if info.id else ""),
            )
            self.objectsTable.setItem(
                row, 2, QTableWidgetItem(info.object_class or ""),
            )
            self.objectsTable.setItem(
                row, 3, QTableWidgetItem(info.key_type or ""),
            )

    def _on_delete(self) -> None:
        pin = self._require_pin()
        if pin is None:
            return
        row = self.objectsTable.currentRow()
        if row < 0:
            self._show_error("Zeile in der Objektliste auswählen.")
            return
        item = self.objectsTable.item(row, 0)
        label = item.text() if item is not None else ""
        if not label:
            self._show_error("Ausgewählte Zeile hat kein Label.")
            return
        if not confirm_destructive(
            self, "Objekt löschen",
            f"Objekt '{label}' unwiderruflich löschen?",
        ):
            return

        def make() -> FunctionWorker:
            def delete():
                with open_exclusive_session(pin) as session:
                    return oc.delete_object(session, label)

            return FunctionWorker(delete)

        def done(found: bool) -> None:
            if found:
                self._show_success(f"Objekt '{label}' gelöscht.")
                self.refresh()
            else:
                self._show_error(f"Kein Objekt mit Label '{label}' gefunden.")

        self._start_worker(make, done, self.deleteButton)

    # --- Erzeugen ---------------------------------------------------------------

    def _on_generate(self) -> None:
        pin = self._require_pin()
        if pin is None:
            return
        key_type = self.genTypeCombo.currentText()
        label = self.genLabelEdit.text().strip()
        if not label:
            self._show_error("Label angeben.")
            return
        id_bytes = self._parse_id(self.genIdEdit.text())
        if id_bytes is None:
            self._show_error("Ungültige ID — Hex-String erwartet (z.B. 01).")
            return

        if key_type == "rsa":
            try:
                bits = int(self.genBitsCombo.currentText())
            except ValueError:
                self._show_error("Ungültige RSA-Länge.")
                return

            def make() -> FunctionWorker:
                def generate():
                    with open_exclusive_session(pin) as session:
                        return oc.generate_rsa_keypair(session, bits, id_bytes, label)

                return FunctionWorker(generate)

        else:
            curve = self.genCurveCombo.currentText()

            def make() -> FunctionWorker:
                def generate():
                    with open_exclusive_session(pin) as session:
                        return oc.generate_ec_keypair(session, curve, id_bytes, label)

                return FunctionWorker(generate)

        def done(info: Any) -> None:
            self._show_success(f"{info.key_type}-Keypair '{label}' erzeugt.")
            self.refresh()

        self.generateProgress.setVisible(True)
        self._start_worker(
            make, done, self.generateButton,
            on_finally=lambda: self.generateProgress.setVisible(False),
        )

    def _on_generate_aes(self) -> None:
        pin = self._require_pin()
        if pin is None:
            return
        label = self.aesLabelEdit.text().strip()
        if not label:
            self._show_error("Label angeben.")
            return
        id_bytes = self._parse_id(self.aesIdEdit.text())
        if id_bytes is None:
            self._show_error("Ungültige ID — Hex-String erwartet (z.B. 01).")
            return
        bits = int(self.aesBitsCombo.currentText())

        def make() -> FunctionWorker:
            def generate():
                with open_exclusive_session(pin) as session:
                    return oc.generate_aes_key(session, bits // 8, id_bytes, label)

            return FunctionWorker(generate)

        def done(info: Any) -> None:
            self._show_success(f"AES-{bits}-Key '{label}' erzeugt.")
            self.refresh()

        self._start_worker(make, done, self.aesButton)

    # --- Schreiben/Lesen ----------------------------------------------------------

    def _on_write_browse(self) -> None:
        path = _ask_open_path(self, "Datei zum Ablegen wählen")
        if path:
            self.writeFileEdit.setText(path)

    def _on_write(self) -> None:
        pin = self._require_pin()
        if pin is None:
            return
        file_path = self.writeFileEdit.text().strip()
        label = self.writeLabelEdit.text().strip()
        if not file_path or not label:
            self._show_error("Datei und Label angeben.")
            return
        id_text = self.writeIdEdit.text().strip()
        id_bytes = self._parse_id(id_text) if id_text else b""
        if id_text and id_bytes is None:
            self._show_error("Ungültige ID — Hex-String erwartet.")
            return
        try:
            data = Path(file_path).read_bytes()
        except OSError as exc:
            self._show_error(f"Datei nicht lesbar ({exc}).")
            return
        if len(data) > oc.MAX_DATA_OBJECT_BYTES:
            self._show_error(
                f"Datei zu groß ({len(data)} Byte, max. "
                f"{oc.MAX_DATA_OBJECT_BYTES})."
            )
            return
        private = self.privateCheck.isChecked()

        def make() -> FunctionWorker:
            def write():
                with open_exclusive_session(pin) as session:
                    return oc.write_data_object(
                        session, data, label,
                        id=id_bytes or None, private=private,
                    )

            return FunctionWorker(write)

        def done(info: Any) -> None:
            protection = "PIN-geschützt" if private else "öffentlich lesbar"
            self._show_success(
                f"Datenobjekt '{label}' geschrieben "
                f"({len(data)} Byte, {protection})."
            )
            self.refresh()

        self._start_worker(make, done, self.writeButton)

    def _on_read(self) -> None:
        pin = self._require_pin()
        if pin is None:
            return
        label = self.readLabelEdit.text().strip()
        if not label:
            self._show_error("Label angeben.")
            return

        def make() -> FunctionWorker:
            def read():
                with open_read_only_session(pin) as session:
                    return oc.read_data_object(session, label)

            return FunctionWorker(read)

        def done(data: bytes) -> None:
            self._last_read_bytes = data
            self.readPreview.setPlainText(data.hex())

        self._start_worker(make, done, self.readButton)

    def _on_read_save(self) -> None:
        if self._last_read_bytes is None:
            self._show_error("Zuerst lesen, dann speichern.")
            return
        path = _ask_save_path(self, "Datenobjekt speichern unter")
        if not path:
            return
        try:
            Path(path).write_bytes(self._last_read_bytes)
        except OSError as exc:
            self._show_error(f"Schreiben fehlgeschlagen ({exc}).")
            return
        self._show_success(
            f"{len(self._last_read_bytes)} Byte nach {path} geschrieben."
        )

    # --- Zufall ---------------------------------------------------------------------

    def _on_random(self) -> None:
        pin = self._require_pin()
        if pin is None:
            return
        num_bytes = self.randomSpin.value()

        def make() -> FunctionWorker:
            def generate():
                with open_read_only_session(pin) as session:
                    return oc.generate_random(session, num_bytes)

            return FunctionWorker(generate)

        def done(data: bytes) -> None:
            self.randomHex.setPlainText(data.hex())

        self._start_worker(make, done, self.randomButton)
