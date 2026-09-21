# Roadmap — pico-hsm-app (Features und Verbesserungen)

Zentrale Sammelstelle für alles noch Offene (Status je Eintrag:
`[beschlossen]`, `[warten auf Hardware]`, `[dauerhaft]`,
`[zurückgestellt]`, `[erledigt]`). Quellen stehen dabei — Details
bleiben dort, keine Duplikate hier.

## 0. Erledigt (Historie, bleibt als Nachweis)

- **Gateway-Kollisions-Check entfernt**: App und Gateway laufen auf
  getrennten Systemen — `check_gateway_reachable`/`GatewayState`,
  `status gateway`, `--gateway-host/port`, GUI-Gateway-Sektion und
  alle Hinweise sind raus (`pkcs11_session.py`, `status.py`,
  `status_tab.py`, `device_mode.py`); `gateway_status.py` → reines
  `audit_log.py`; Konzept §7/§10 umgeschrieben, Phase-2-Anhang-A
  unverändert.

## 1. Windows-Installer `[beschlossen, noch nicht umgesetzt]`

Standalone-App: `PicoHSM-Setup-0.x.exe` → installieren → Startmenü
„Pico HSM" → öffnen und benutzen. Entscheidungen: nur Windows,
Installer (kein Portable), manuelle Updates (kein Shamir/ssss mehr —
Empfänger-Modus braucht nur `age`).

Bausteine:

1. **PyInstaller-Bundle** (`pico-hsm-gui.spec`, onedir):
   `gui.app:main` als Entry, `cli` als zweite Console-App im selben
   Bundle; Hidden-Imports (`smartcard`, `pkcs11`, `qfluentwidgets`),
   Qt-Plugins/DLLs automatisch; `age.exe` + selbstgebautes
   `picotool.exe` + `libusb-1.0.dll` als Binaries ins Bundle (App
   findet sie im eigenen Ordner statt PATH — Suchpfad-Fallback in
   `flash_core`/`backup_core`, nur Windows).
2. **Binary-Pinning**: exakte Quellen/Hashes (age-GitHub-Release,
   picotool v2.3.1-Selbstbau wie in `docs/02-setup.md`, OpenSC-MSI-
   Version) in neuer `docs/16-release.md`; Build-Skript prüft Hashes.
3. **Inno Setup-Installer** (`installer.iss`): Startmenü,
   Desktop-Icon (`.ico` erstellen), Deinstallation; Preflight-Seiten:
   Smartcard-Dienst läuft? OpenSC gefunden (`opensc-pkcs11.dll`,
   sonst Hinweis-Link auf MSI)? `age` vorhanden?
4. **App-seitige Start-Checks**: eine Stelle beim Start mit
   verständlichen Meldungen statt Crash (picotool/age/OpenSC
   je Feature — heute verstreut als `FlashError`/`BackupError`).
5. **Lizenzen**: GPLv3 durch QFluentWidgets (`NOTICE.md`) — Installer
   legt Lizenz + Corresponding-Source-Hinweis (Repo-Link) bei.
6. **Release-Ablauf**: Version taggen → PyInstaller → Inno →
   Smoke-Test auf frischer VM (installieren, Board anstecken, Login,
   Status) → Setup.exe + Hash veröffentlichen. Updates = neue
   Setup.exe drüber installieren (`~/.pico_hsm` bleibt erhalten).

## 2. Hardware-Verifikation `[fast abgeschlossen — 18/20, Rest unten]`

Die Kampagne ist durch (`docs/15-real-hardware-validation-checklist.md`, `hw-logs/01-20`): Umgebung,
Init, DynOpts-Roundtrip, Daten/EC/RSA-Roundtrips, PIN-Zyklus,
Sharing, Preflight, TOTP, Re-Flash — alle abgehakt. Verifiziert und
deshalb hier **gestrichen** (Code-Kommentare gleich mit nachgezogen):
OTP-Fingerprint (64× `0`), DynOpts-Masken, Objekt/EC/RSA-Flows,
PIN-Zyklus, Sharing (kein OS-Lock). Offen nur noch:

- **DKEK-Voll-Flow** `[erledigt 2026-09-21, hw-logs/21]`: Wipe +
  Re-Init, Share-Zeremonie, Wrap/Unwrap am Board bewiesen. Offen nur
  noch: versiegelter Roundtrip per Empfänger (`hw-logs/22` deckt
  Export/Restore-Logik ohne Seal-Schicht ab).
- **P2C-Enforcement-Umfang**: kein Tastendruck trotz aktivem P2C
  beobachtet (kein `ENABLE_EMULATION` im Build) — Timing-Test
  nachholen (Checkliste Phase 2.1).
- **Manifest-Felder/Backup-Roundtrip**: entfällt auf Windows (kein
  `ssss`; Checkliste 12) — host-seitig auf Linux bereits verifiziert;
  Manifest-Gegenprüfung gegen echte `backup-split.sh`-Ausgabe steht
  noch aus (`backup_index.py:11`).
- **SCard-Edge**: natives Verhalten ohne laufenden Smartcard-Dienst
  nie getestet (reiner Doku-Hinweis heute).

## 3. Bewusste Einschränkungen `[dauerhaft, gelten bis auf weiteres]`

Keine Bugs — Design-Entscheide mit Fundstelle:

- DKEK-Shares erzeugen/importieren bleibt CLI-only (braucht
  interaktives Custodian-Terminal, `dkek_core.py:10`,
  DKEK-Tab/Wizard verweisen nur dorthin).
- Kein `setup set ...`: OTP-Flags sind One-Way-Schalter
  (Thermometer-Code, Brick-Risiko) — nur manueller Ablauf mit
  Ersatzboard-Test (`setup.py:139`).
- Kein `keys sign/derive/...`: laufende Crypto-Ops gehören dem
  HSM-API-Gateway, nicht der App (`docs/01-nutzung.md`, `objects_core.py:15`;
  Whitelists RSA/EC/AES bleiben).
- APDU strikt sequenziell (nie parallel zu offener PKCS#11-Session),
  genau ein Reader mit Karte ohne `--reader`
  (`apdu_core.py:269,278,308`); `pyscard` Pflicht für
  `setup dynamic-options`.
- Gateway-Check ohne `host/port` liefert immer `reachable=False`
  (rein informativ, blockiert nichts; Config aus Konzept-§10
  existiert nicht — `pkcs11_session.py:121`).

## 4. Geparkt `[zurückgestellt, Reihenfolge offen]`

- **BOOTSEL-Einstieg ohne Token**: Login-Dialog kennt nur Tokens;
  reiner Firmware-Betrieb (BOOTSEL, kein Login) hat keinen Weg in
  die GUI — „Trotzdem öffnen (BOOTSEL)"-Pfad nachrüsten.
- **Geräte-Auswahl für `init`/`dkek`**: `sc-hsm-tool` hat keinen
  verifizierten Geräte-Selektor — heute: nur Ziel-Board
  anschließen (`docs/01-nutzung.md`).
- **APDU-Reader-Auswahl in der GUI**: `--reader` existiert nur im
  CLI; Setup-Tab nutzt weiter Automatik (PC/SC-Namensraum ≠ Serien-
  nummern, `docs/01-nutzung.md`).
- **Auto-Update**: bewusst zurückgestellt (manuelle Setup.exe,
  siehe oben) — braucht Signierung + Update-Kanal.
- **ssss entfernt** `[erledigt]`: Bundle-Option verworfen (GMP/POSIX-
  Hürde, kein MSVC ohne Sicherheits-Flickwerk), danach kompletter
  ssss-Code entfernt (Core, CLI-, GUI-Datei-Flows, Tests, Doku) —
  nur Empfänger-Modus (volle Kopien, 1-aus-n per age).
- **Python-Shamir (später)**: GF(256)-Implementierung mit
  ssss-kompatiblem Share-Format als mögliche Verbesserung, wenn
  echtes m-von-n ohne externes Binary nötig wird (Interop-Tests
  gegen echtes `ssss` als Referenz einplanen).
- **Phase 2 Diagnose-Modus** (Konzept-Anhang A, `docs/03:594-707`):
  `gateway_client.py` als 1:1-Spiegel von `protocol.rs` (6 Ops,
  Stdlib-ssl/socket, JSON-pro-Zeile), eigener Key-Label-Scope,
  `gateway_client.toml` (chmod 600), Contract-Test via
  Rust-`JsonSchema`-Export → `protocol-schema.json`; braucht
  laufende `pico-hsm-api-connector`-Instanz mit Client-Zertifikat
  (nicht Teil dieses Repos). Erst nach Phase 1 bei Bedarf.
- **Doku-Fix**: `extra_command.md` Key-Usage-Counter-`SET`
  (`80 64 06 00 01 01` ist Copy-Paste, implementiert ist
  `... 01 02` — `apdu_core.py:51`).
