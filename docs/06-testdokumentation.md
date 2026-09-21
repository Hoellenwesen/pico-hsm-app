# Testdokumentation — pico-hsm-app

Stand: **344 Tests**, alle ohne Hardware lauffähig (über Mocks,
Simulationen und echte externe Binaries — siehe §3). Diese Datei ist
die **einzige Quelle der Wahrheit** für Teststand, -abdeckung und
-verfahren.

## 1. Test-Suite im Überblick

```bash
python -m pytest tests/ -q     # 344 Tests, alle ohne Hardware
```

| Testdatei | Fokus | Tests |
|---|---|---|
| `tests/test_apdu_core.py` | APDU-/Dynamic-Options-Verarbeitung | 28 |
| `tests/test_backup_hygiene.py` | Backup-Alter/Drill-Status/Vollständigkeit | 9 |
| `tests/test_cli_wiring.py` | CLI-Registrierung, Exit-Codes, JSON | 9 |
| `tests/test_console_encoding.py` | Windows-cp1252-/ASCII-Stabilität | 2 |
| `tests/test_dashboard.py` | Empfehlungs-Matrix (Priorität, Sprung-Ziele) | 8 |
| `tests/test_device_mode.py` | Modus-Sensor, CLI-Gates, GUI-Sektions-Gating | 11 |
| `tests/test_dkek_core.py` | DKEK Shares, Wrap/Unwrap, Status | 13 |
| `tests/test_flash_core.py` | Firmware-Preflight, Signatur, Rollback, Audit | 22 |
| `tests/test_gui_backup_tab.py` | Backup-Tab (HSM-Backup, Liste/Hygiene) | 13 |
| `tests/test_gui_dkek_tab.py` | DKEK-Tab (Status/Wrap/Unwrap, Vault) | 12 |
| `tests/test_gui_firmware_tab.py` | Firmware-Tab (Preflight/Flash/TOTP) | 11 |
| `tests/test_gui_keys_tab.py` | Schlüssel-Tab (Liste/Löschen/Erzeugen, Vault) | 19 |
| `tests/test_gui_logs_tab.py` | Logs-Tab (reines Audit-Log, Limit, Chain, Switch) | 6 |
| `tests/test_backup_recipients.py` | Empfänger-Modus (Split/Restore/Drill) | 9 |
| `tests/test_hsm_backup.py` | HSM-Backup-Core + CLI (Gate, Export, Seal, Restore, Quirk) | 19 |
| `tests/test_gui_pin_tab.py` | PIN-Tab (Ändern/Entsperren/Status, Vault) | 12 |
| `tests/test_gui_setup_tab.py` | Setup-Tab (OTP/DynOpts-Sektionen) | 8 |
| `tests/test_gui_skeleton.py` | GUI-Skeleton (Fenster, Sidebar, Dialoge) | 14 |
| `tests/test_gui_status_tab.py` | Status-Tab (Gerät/PIN, Empfehlungen) | 15 |
| `tests/test_gui_wizard_tab.py` | Wizard-Tab (Schritte, Gating, State, Aktionen) | 17 |
| `tests/test_info_bars.py` | InfoBar-Aufräumen (X-gelöschte Bars) | 3 |
| `tests/test_login_dialog.py` | Login-Dialog (Panels, Entsperren, Wege) | 8 |
| `tests/test_multi_device.py` | Token-/Reader-Auswahl, CLI-Flags, GUI-Helper | 10 |
| `tests/test_paths_home_isolation.py` | Home-Isolation (PICO_HSM_HOME, kein Schreiben ins echte Home) | 5 |
| `tests/test_objects_core.py` | Objekt-Lifecycle (Keys, AES, Datenobjekte) | 35 |
| `tests/test_pin_core.py` | PIN-Verwaltung | 10 |
| `tests/test_pin_vault.py` | Vault-Cache/Timeout/Sperren-Button (+LockedError) | 6 |
| `tests/test_pkcs11_session.py` | PKCS#11-Session-/Belegt-Handling (ohne Gateway) | 10 |
| **Summe GUI** (alle `test_gui_*`) | | **127** |
| **Gesamt** | | **344** |

## 2. Test-Strategie

- Alle Tests laufen ohne Hardware (nur Mocks/Simulationen + echte
  externe Binaries für `age`/`picotool`/`sc-hsm-tool`, die
  **keine** PKCS#11-Session öffnen).
- Hardware-Tests sind in `docs/15-real-hardware-validation-checklist.md`
  (Checkliste, Vorgehen am Board) und hier (Python-Suite) getrennt —
  die Python-Suite ist **immer** ohne Board lauffähig.

## 3. Verifizierungsverfahren

- **Unit-/Mock-Tests:** Core-Module gegen simulierte APDU-/picotool-
  Ausgaben; PIN-/DKEK-/Objekt-Logik gegen Mocks.
- **Empirisch end-to-end (echte Binaries):** `age`-Encrypt/Decrypt,
  `picotool`-Preflight gegen signierte UF2-Datei,
  `sc-hsm-tool`-DKEK-Shares.
- **Gegen offizielle Dokumentation verifiziert:** CLI-Syntax (README,
  `usage.md`), PKCS#11-API-Referenz, `init token`-Manpage.
- **Bekannte offene Punkte (ohne echtes Board):** Siehe Abschnitt 4.

## 4. Test-Gleichgewicht: Was Mocks NICHT abdecken

- Das echte `picotool info`-/-`seal`-Verhalten (Version/Rollback,
  Signatur) wird mit **simulierter Ausgabe** getestet; die tatsächlichen
  Feldnamen in `get_burned_key_fingerprint()` sind am Board belegt
  (64× `0` auf unberührtem Board, `docs/15-...` Phase 1). Bei
  Abweichung: kontrollierter Abbruch statt falschem Fingerprint.
- `pin change/unblock`, `init token`, `keys list/delete/generate`:
  Syntax plus Hardware-Roundtrips am Board verifiziert (`docs/15-...`
  Phase 2, `hw-logs/`). Offen nur `dkek wrap/unwrap` im Voll-Flow
  (Checkliste 7 offen, Teilerfolg `hw-logs/16-...`).
- `keys list` Key-Ref: wird nicht aus `python-pkcs11` ausgelesen —
  dafür zusätzlich `pkcs15-tool -D` nötig.
