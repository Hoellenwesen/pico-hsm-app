# Nutzung — pico-hsm-cli und pico-hsm-gui bedienen

Vollständige Syntax pro Befehl: `pico-hsm-cli <gruppe> <kommando> --help`.
Diese Doku gibt die Kommandogruppen, die wichtigsten Beispiele und das
Sicherheitsmodell — sie erhebt **keinen Anspruch auf Vollständigkeit**
der Optionen (das ist Aufgabe des CLI-`--help`).

Exit-Codes: `0` = OK, `1` = erwarteter/abgelehnter Fall
(z. B. abgelehnte Bestätigung, gebrochene Audit-Chain, zu wenige
Shares), `2` = unerwarteter Fehler. Im `--json`-Modus erscheinen
Fehler als `{"error": …}` auf stderr.

## 1. Kommandogruppen

| Gruppe | Zweck | Wichtigste Unterkommandos |
|---|---|---|
| `status` | Read-only, **kein** PKCS#11-Lock | `device`, `audit`, `all` |
| `init` | Token-Ersteinrichtung | `token` |
| `setup` | OTP-Anzeige, Dynamic Options | `show`, `dynamic-options get/set` |
| `pin` | PIN-Verwaltung | `change`, `unblock`, `status` |
| `dkek` | DKEK (Shares, Key-Wrap/Unwrap) | `create-share`, `import-share`, `wrap-key`, `unwrap-key`, `status` |
| `keys` | Objekte (Keys, Datenobjekte) | `list`, `delete`, `generate`, `generate-aes`, `write-object`, `read-object`, `random`, `import` |
| `backup` | HSM-Backup + Backup-Liste (Empfänger-Modus, siehe unten) | `hsm-backup`, `hsm-restore`, `list` |
| `firmware` | Verifiziertes Firmware-Update | `preflight`, `flash`, `audit tail/verify` |

Globale Flags (für alle Befehle): `--pkcs11-lib <pfad>`, `--json`,
`--force`, `--pin-env <VAR>`, `--serial <nummer>`, `--reader <name>`,
`-v/--verbose`.

Mehrere Geräte: `--serial` wählt das Token für PKCS#11-Befehle
(`status device`, `keys`, `pin`, `setup show` meldet bei mehreren
Tokens die Nummern zur Auswahl); `--reader` wählt den PC/SC-Reader
für `setup dynamic-options`. In der GUI merkt sich das
Token-Dropdown im Status-Tab die Auswahl (alle Tabs nutzen sie).
`init`/`dkek` (via `sc-hsm-tool`) haben keine Geräte-Auswahl —
bei mehreren Karten bitte nur das Ziel-Board anschließen.

## 2. Typische Abläufe

### Erst-Inbetriebnahme

```bash
pico-hsm-cli init token                        # Token ersteinrichten (LÖSCHT alles!)
pico-hsm-cli pin status                        # initialized/Retry/Sperrstatus
pico-hsm-cli dkek status                       # DKEK-Shared-Status
pico-hsm-cli setup show                        # OTP + RTC-Äquivalent + DynOpts
```

### PIN-Verwaltung (kritisch, **niemals** als Klartext-Argument)

```bash
pico-hsm-cli pin change                        # interaktiv (getpass)
pico-hsm-cli pin unblock --puk-env SO_PIN      # gesperrte User-PIN mit SO-PIN
pico-hsm-cli pin status
```
PINs werden nie auf Disk geschrieben und nie als Argument übergeben —
nur interaktiv (`getpass`) oder aus einer Umgebungsvariable
(`--pin-env VAR`, Name frei wählbar).

### DKEK — Device Key Encryption Key

```bash
pico-hsm-cli dkek create-share <file>          # Share erzeugen (interaktiv)
pico-hsm-cli dkek import-share <file>          # Share importieren
pico-hsm-cli dkek wrap-key <out> --key-reference N    # Key-Backup
pico-hsm-cli dkek unwrap-key <wrapped> --key-reference N  # Key-Restore [--force]
pico-hsm-cli dkek status
```
DKEK-Wrap/Unwrap nutzt `sc-hsm-tool`. Hinweis: Shares sichern das
**DKEK-Passwort**, nicht die Anwendungs-/PQC-Keys (dafür ist `backup`
da).

### Schlüssel erzeugen/lesen/löschen

```bash
pico-hsm-cli keys list
pico-hsm-cli keys generate --type rsa --bits 2048 --id 01 --label mykey
pico-hsm-cli keys generate --type ec --curve secp256r1 --id 02 --label myec
pico-hsm-cli keys generate-aes --bits 256 --id 03 --label myaes
pico-hsm-cli keys write-object <datei> --label mycert   # Datenobjekt
pico-hsm-cli keys read-object --label mycert
pico-hsm-cli keys delete --label mykey
```
RSA-Längen 1024/2048/4096 (2048 ≈ 2:45 min, 4096 ≈ 15 min — lang!).
Bewusst **kein** `keys sign/derive` — laufende Crypto-Ops bleiben
Aufgabe des HSM-API-Gateways (§ Diagnose, Phase 2).

### Backup-Liste (Hygiene)

```bash
pico-hsm-cli backup list <parent-dir>
```

Listet Backup-Verzeichnisse mit Alter, Drill-Abstand und Hygiene
(Vollständigkeit, Ciphertext-SHA). Reine Datei-Kommandos
(`split`/`restore`/`drill`) sind entfernt — Backups laufen über den
HSM-Flow, Dateien direkt per `age` (Empfänger-Modus, 1-aus-n).

### HSM-Backup — Token-Inhalt (Hardware-Ausfall)

```bash
pico-hsm-cli backup hsm-backup <out-dir> --recipient <age-pubkey> [--recipient ...] [--no-keys/--no-data/--no-options] [--key-ref LABEL:REF]
pico-hsm-cli backup hsm-restore <backup-dir> --identity-file <id.txt> [--force]
```

Sichert Keys (DKEK-Wrap), Datenobjekte und Dynamic Options vom Token
(Vollbackup per Default, Teile per `--no-*` abwählbar) und versiegelt
das Bundle per Empfänger (1-aus-n). Restore auf neuer Hardware: Token
initialisieren + derselbe DKEK per Shares importieren, dann Unwrap,
Daten, Optionen + Verifikation gegen Manifest. Braucht DKEK mit
Shares (sonst Abbruch mit Anleitung). PINs, DKEK und OTP migrieren
nie — GUI: Backup-Tab, Sektion „HSM-Backup" (Checkboxen
Vollbackup/Benutzerdefiniert).

### Firmware — verifiziertes, signiertes Update

```bash
pico-hsm-cli firmware preflight <datei.uf2>    # nur prüfen (Board-Fingerprint,
                                               #  Signatur, Anti-Rollback, TOTP)
pico-hsm-cli firmware flash <datei.uf2>        # Preflight + TOTP + Flash + Audit
pico-hsm-cli firmware audit tail [-n N]        # Audit-Log
pico-hsm-cli firmware audit verify             # Hash-Chain-Integrität
```
Preflight prüft in dieser Reihenfolge: Board-Identität (OTP-
Fingerprint), Firmware-Signatur, Anti-Rollback (Version/Rollback
gegen letzte bekannte), optional TOTP. **Funktioniert nur mit
BOOTSEL-Modus + angeschlossenem Board und signierter Datei.**
Der TOTP-Code wird von einem physisch getrennten Gerät abgefragt.

## 3. GUI — `pico-hsm-gui`

Start mit `pico-hsm-gui` (Dark-Default). Sidebar mit 9 Tabs (Start-
Wizard, 7 CLI-Gruppen + Logs als Diagnose-Übersicht). Die App erkennt
den Geräte-Modus automatisch (alle ~3s) und sperrt Unnutzbares strikt —
mit Tooltip statt Blind-Fehler:

| Tab | Inhalt | Modus |
|---|---|---|
| Start | Einrichtungs-Assistent (neu: alle Schritte; eingerichtet: nur offene; Fortschritt je Board) | immer |
| Status | Gerät+PIN-Status (kompakt, Trennlinien; Auto-Refresh alle 60s + Button; Token-Dropdown; Empfehlungen mit Sprung-Buttons) | immer (Device-Teil nur Normal) |
| Setup | OTP-Anzeige (BOOTSEL) + Dynamic Options (Normal+Login, getrennte Sektionen) | BOOTSEL / Normal |
| PIN | Ändern, Entsperren, Status — Felder verdeckt/geleert | Normal |
| DKEK | Status, Ways, Unwrap/Import | Normal |
| Schlüssel | Liste, Löschen, Import-Hinweis, RSA/EC-Erzeugung, Objekte, Zufall (PIN aus Anmeldung) | Normal (+Anmeldung) |
| Backup | Empfänger-Split/Restore/Drill/Liste mit Hygiene (Alter, Drill-Abstand, Vollständigkeit) | immer (Software-only) |
| Firmware | Preflight, geführter Flash mit TOTP-Dialog (Audit siehe Logs-Tab) | BOOTSEL / Normal |
| Logs | Nur Audit-Log mit Hash-Chain-Status | immer |

CLI-Gates nennen bei falschem Modus die nötige Aktion: `setup show` und
`firmware flash` brauchen BOOTSEL, `setup dynamic-options` braucht Normal.

PIN-Anmeldung (GUI): Beim Start erscheint der Anmelde-Dialog —
Board aus der Liste wählen, dann je Zustand: neues Board (keine
User-PIN) → „Assistent öffnen" (Start-Tab); bekanntes Board →
User-PIN + „Entsperren" (Fehlversuche zählen!). Die PIN
liegt danach nur im RAM (Vault), Tabs nutzen sie ohne Neu-Eingabe.
Nach 15 Minuten ohne Nutzung oder per „Sperren" (Sidebar unten) wird
sie gelöscht. BOOTSEL-Boards erscheinen als Hinweis (kein Login
möglich — Anmeldung erst im Normal-Modus).

GUI-Sicherheitsregeln: PIN/TOTP-Felder immer verdeckt + nach Erfolg
geleert; destruktive Aktionen haben Confirm-Dialoge; lange Operationen
laufen nie im UI-Thread. QFluentWidgets steht unter GPLv3 — bei
Weitergabe beachten.

## 4. Sicherheitsmodell in Kürze

- **Firmware-Flash**: Board-Fingerprint → Signaturprüfung →
  Anti-Rollback-Check → optional TOTP (Secret nur auf getrenntem
  Gerät) → Flash → manipulationssicheres Hash-Chain-Audit-Log.
- **PIN**: nie als Klartext-Argument, nie auf Disk — interaktiv oder
  per env.
- **Backup**: age-Verschlüsselung für Empfänger (1-aus-n, volle
  Kopien pro Standort);
  Ciphertext-Prüfsumme vor, Plaintext-Prüfsumme nach dem Restore —
  Manipulation wird erkannt.
- **Session-Konflikt**: schlägt das Öffnen einer schreibenden Session
  mit einer Fehlerklasse fehl, die auf einen belegten Reader hindeutet,
  meldet die App einen belegten Reader (anderer lokaler Prozess). App
  und Gateway laufen auf getrennten Systemen — es gibt keinen
  Erreichbarkeits-Check mehr.

Details & Begründungen: `docs/03-architektur-konzept.md`.
