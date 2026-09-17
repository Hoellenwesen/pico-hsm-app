# pico-hsm-cli

Konfigurations-CLI für den **Pico-HSM-Teil** des PicoHSM-Projekts
(Homelab-HSM auf Basis Raspberry Pi Pico 2 / RP2350, Firmware: eigener
Fork [`Hoellenwesen/pico-hsm`](https://github.com/Hoellenwesen/pico-hsm),
Upstream `polhenarejos/pico-hsm`). Deckt den kompletten Lebenszyklus ab:
Token-Ersteinrichtung, PIN-Verwaltung, DKEK-Shares, Schlüsselverwaltung,
Backup/Restore und verifiziertes Firmware-Update — alles über eine
einzige, konsistente Kommandozeile.

Aktuell im Umbau zu einer App mit primärer GUI und optionalem CLI-Modus,
die den vollen Firmware-Funktionsumfang abdeckt (Keypair-/AES-Erzeugung,
Zertifikat-/Datenobjekte, RTC, Dynamic Options, Diagnose-Modus über das
HSM-Gateway) — siehe das Architekturkonzept-Dokument für Details und
Umsetzungsstand. Diese README beschreibt den aktuellen CLI-Stand; sie
wird mit fortschreitendem Umbau aktualisiert.

---

## 1. Architektur-Entscheidungen

- **Python statt Rust/Bash**: Flaschenhals ist immer der Subprozess/das
  HSM, nie die App-Logik — eine Systemsprache bringt keinen spürbaren
  Gewinn. Externe Krypto-Tools (`age`, `ssss`, `sc-hsm-tool`, `picotool`)
  bleiben externe Programme — nur die Orchestrierung ist Python, keine
  Reimplementierung von Kryptografie in Python.
- **`click`** als CLI-Framework (verschachtelte Subcommand-Gruppen,
  automatische Hilfetexte, saubere Optionen-Validierung).
- **Eine Quelle der Wahrheit pro Sicherheitsbereich**: `flash_core.py`
  für alles Firmware-Update-Relevante, `backup_core.py` für alles
  Backup/Restore-Relevante — keine Logik-Duplikate zwischen CLI-Befehlen.
- **Gateway-bewusster Konflikt-Check**: Der frühere lokale
  `pico-hsm-daemon` (Unix-Socket) ist laut
  `pico-hsm-api-connector/MIGRATION.md` komplett durch das HSM-Gateway
  (mTLS-Netzwerkdienst, exklusiver PKCS#11-Konsument für laufende
  Crypto-Ops) abgelöst. Diese CLI prüft daher zweistufig: ein rein
  informativer TCP-Erreichbarkeits-Check gegen das Gateway
  (`--gateway-host`/`--gateway-port`) plus — beim tatsächlichen Öffnen
  einer schreibenden PKCS#11-Session — eine gezielte Behandlung der
  Fehlerklassen, die auf einen belegten Reader hindeuten. Anders als
  früher gibt es kein „trotzdem fortfahren“ mehr: der Fehler ist dann
  bereits real aufgetreten, ein Bestätigungsdialog würde nichts ändern.
- **PIN-Handling**: nie als Klartext-Argument, nie auf Disk — interaktiv
  per `getpass` oder aus Umgebungsvariable (`--pin-env <VAR>`, Name frei
  wählbar).

---

## 2. Setup

```bash
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e .
```

### Externe Abhängigkeiten (keine Python-Pakete)

| Programm | Zweck | Paket (Debian/Ubuntu) |
|---|---|---|
| `picotool` (mit USB-Support!) | Firmware-Preflight/Flash, OTP-Anzeige | selbst bauen — **nicht** die CMake-Auto-Download-Variante, die hat keinen USB-Support |
| `opensc-pkcs11.so`/`.dll` | PKCS#11-Modul für `python-pkcs11` | `opensc` |
| `pkcs11-tool` | PIN-Verwaltung | `opensc` |
| `sc-hsm-tool` | DKEK-Shares, Token-Init, Key-Wrap/Unwrap | `opensc` |
| `age`, `age-keygen` | Backup-Verschlüsselung | `age` |
| `ssss-split`, `ssss-combine` | Shamir Secret Sharing für Backups | `ssss` |

```bash
sudo apt install opensc age ssss
```

`picotool` mit USB-Support selbst bauen (siehe Abschnitt 5, dort exakt
so gebaut und getestet):

```bash
git clone --depth 1 https://github.com/raspberrypi/pico-sdk.git
cd pico-sdk && git submodule update --init --depth 1 && cd ..
git clone --depth 1 https://github.com/raspberrypi/picotool.git
cd picotool && mkdir build && cd build
cmake .. -DPICO_SDK_PATH=../../pico-sdk
make -j$(nproc)
# ./picotool ins PATH legen
```

---

## 3. Funktionsübersicht

Globale Flags (für alle Befehle):

| Flag | Zweck |
|---|---|
| `--pkcs11-lib <pfad>` | OpenSC-PKCS#11-Modulpfad überschreiben (Windows/Linux/macOS) |
| `--json` | maschinenlesbare Ausgabe für Skripting |
| `--force` | Bestätigungen überspringen |
| `--pin-env <VAR>` | PIN aus Umgebungsvariable statt Prompt |
| `--gateway-host <host>` / `--gateway-port <port>` | Adresse des HSM-API-Gateways für den rein informativen Erreichbarkeits-Check (siehe `status gateway`, Konflikt-Hinweise). Ohne Angabe gilt das Gateway als nicht erreichbar, blockiert aber nichts. |
| `-v/--verbose` | Debug-Ausgaben auf stderr |

Exit-Codes: `0` = OK, `1` = erwarteter/abgelehnter Fall, `2` = unerwarteter Fehler.

### `init` — Token-Ersteinrichtung

```
pico-hsm-cli init token [--dkek-shares N] [--pin-retry N] [--label L]
```
Initialisiert das Token auf SmartCard-HSM/PKCS#15-Ebene (SO-PIN, initiale
User-PIN, optional Anzahl DKEK-Shares). **Löscht alle vorhandenen Keys**
— verlangt explizite Bestätigung. Nutzt `sc-hsm-tool --initialize`.

### `status` — read-only, kein PKCS#11-Lock

```
pico-hsm-cli status device   # Token-Label, Modell, Seriennummer
pico-hsm-cli status gateway  # Ist das HSM-API-Gateway erreichbar? (rein informativ)
pico-hsm-cli status audit    # Hash-Chain-Integrität + letzte Audit-Einträge
pico-hsm-cli status all      # alle drei kombiniert
```

### `setup` — OTP-Anzeige, RTC, Dynamic Options

```
pico-hsm-cli setup show
pico-hsm-cli setup datetime get
pico-hsm-cli setup datetime set 2026-09-16T12:00:00  # oder --now
pico-hsm-cli setup dynamic-options get
pico-hsm-cli setup dynamic-options set --press-to-confirm --key-usage-counter
```
Secure-Boot-Pubkey-Fingerprint, Anti-Rollback-Status, Debug-Lock-Status,
aktueller Rollback-Zähler (`show`). Bewusst **kein** `setup set` für
OTP-Flags — One-Way-Schalter mit Brick-Risiko; das Setzen bleibt dem
dokumentierten manuellen Ablauf mit Ersatzboard-Test vorbehalten.
`datetime`/`dynamic-options` nutzen Vendor-APDUs direkt über `pyscard`
(`pico_hsm_tools/apdu_core.py`), kein `opensc-tool`-Textparsing.
⚠️ Nicht gegen echte Hardware verifiziert (kein Board vorhanden);
`get_dynamic_options` ist aus dem APDU-Schema abgeleitet, nicht aus
einem dokumentierten Beispiel. Das Deaktivieren von Press-to-Confirm
verlangt eine explizite Bestätigung (außer `--force`).

### `pin` — PIN-Verwaltung

```
pico-hsm-cli pin change                    # User-PIN ändern
pico-hsm-cli pin unblock [--puk-env VAR]   # gesperrte PIN mit SO-PIN zurücksetzen
pico-hsm-cli pin status                    # Retry-Zähler/Sperrstatus (TokenFlag-Bitmaske)
```
Nutzt `pkcs11-tool`, da `python-pkcs11` keine PIN-Management-API besitzt.

### `dkek` — Device Key Encryption Key

```
pico-hsm-cli dkek create-share <file> [--threshold M --total N]
pico-hsm-cli dkek import-share <file> [--total N]
pico-hsm-cli dkek wrap-key <out-file> --key-reference N     # Key-Backup
pico-hsm-cli dkek unwrap-key <wrapped-file> --key-reference N  # Key-Restore
pico-hsm-cli dkek status    # Anzahl importierter Shares, Key-Check-Value
```
Nutzt `sc-hsm-tool`. Das `--threshold`/`--total`-Schema von `sc-hsm-tool`
splittet nur das **Passwort eines einzelnen DKEK-Shares** unter mehreren
Custodians — unabhängig vom projekteigenen Shamir/age-Schema unter
`backup` (das schützt die PQC-Keys, nicht die DKEK).

### `keys` — Schlüsselverwaltung

```
pico-hsm-cli keys list                # Label, Klasse, Key-Typ
pico-hsm-cli keys delete <label>      # mit Bestätigung, außer --force
pico-hsm-cli keys import              # verweist auf `dkek unwrap-key`
pico-hsm-cli keys generate --type rsa --bits 2048 --id 01 --label mykey
pico-hsm-cli keys generate --type ec --curve secp256r1 --id 02 --label myec
pico-hsm-cli keys generate-aes --bits 256 --id 03 --label myaes
pico-hsm-cli keys write-object <datei> --label mycert [--id 04] [--not-private]
pico-hsm-cli keys read-object --label mycert [--out datei]
pico-hsm-cli keys random <anzahl-bytes>   # Hex-Ausgabe, max. 1024 Byte
```
RSA-Längen: 1024/2048/4096 (2048 >20s, 4096 >20min — CLI blockiert);
EC-Kurven: secp192r1/secp256r1/secp384r1/secp521r1/secp192k1/secp256k1/
brainpoolP256r1/brainpoolP384r1/brainpoolP512r1. Datenobjekte max.
4096 Byte, Default PIN-geschützt (`--not-private` = öffentlich lesbar).
Bewusst **kein** `keys sign`/`keys derive` — laufende kryptografische
Operationen bleiben Aufgabe des HSM-API-Gateways (`pico-hsm-api-connector`).
Ein reiner Diagnose-Modus über das Gateway (kein direkter PKCS#11-Zugriff
auf Live-Keys) ist als eigene `diagnose`-Kommandogruppe geplant.

### `backup` — Shamir Secret Sharing + age

```
pico-hsm-cli backup split <datei> <out-dir> -m 3 -n 5 [--identity-file <key>]
pico-hsm-cli backup restore <backup-dir> <output-datei> [--share ... --share ...]
pico-hsm-cli backup drill --self-test
pico-hsm-cli backup drill <backup-dir> [--share ...]
pico-hsm-cli backup list <parent-dir>
```
Verschlüsselt mit `age` (Identity-Keypair, nicht Passphrase-Modus), der
private Key wird per Bech32-Dekodierung auf 32 rohe Byte reduziert und
mit `ssss-split` m-von-n gesplittet (ssss-Limit: 64 Byte — der volle
74-Zeichen-Identity-String passt nicht direkt). `split` unterstützt
sowohl automatische Keypair-Generierung als auch `--identity-file` für
ein vorhandenes Keypair. `restore` prüft die Ciphertext-Prüfsumme **vor**
und die Plaintext-Prüfsumme **nach** dem Entschlüsseln.

### `firmware` — verifiziertes Update

```
pico-hsm-cli firmware preflight <datei.uf2>   # nur prüfen, nicht flashen
pico-hsm-cli firmware flash <datei.uf2>       # Preflight + TOTP + Flash + Audit-Log
pico-hsm-cli firmware audit tail [-n N]
pico-hsm-cli firmware audit verify            # Hash-Chain-Check (z.B. Wazuh-Cronjob)
```
Prüft in dieser Reihenfolge: Board-Identität (OTP-Pubkey-Fingerprint),
Signatur, Anti-Rollback, optional TOTP-Autorisierung (Secret nur auf
physisch getrenntem Gerät), dann Flash + manipulationssicheres
Hash-Chain-Audit-Log.

---

## 4. Implementierung — Modulübersicht

```
cli/
├── main.py              # click-Gruppe, bindet alle Subcommands ein
├── context.py            # CliContext: PIN-Handling, --json, --force, Exit-Codes
├── pkcs11_helpers.py      # verbindet CliContext mit Gateway-/Session-Konflikt-Check
└── commands/
    ├── init.py             # Token-Ersteinrichtung (sc-hsm-tool --initialize)
    ├── status.py           # read-only
    ├── setup.py            # OTP-Anzeige, RTC-Datetime, Dynamic Options (apdu_core.py)
    ├── pin.py              # PIN-Verwaltung (pkcs11-tool)
    ├── dkek.py             # DKEK-Shares, Key-Wrap/Unwrap (sc-hsm-tool)
    ├── keys.py             # Objektverwaltung (objects_core.py)
    ├── backup.py           # Backup/Restore/Drill/List (backup_core.py)
    └── firmware.py         # Preflight/Flash/Audit (flash_core.py)

pico_hsm_tools/
├── flash_core.py         # EINZIGE Quelle der Wahrheit: Firmware-Update-Sicherheit
├── pkcs11_session.py      # exklusive/read-only Sessions, Gateway-/Session-Konflikt-Check
├── gateway_status.py       # read-only Monitoring (Gateway-Erreichbarkeit, Audit-Tail)
├── pin_core.py            # EINZIGE Quelle der Wahrheit: PIN-Verwaltung (pkcs11-tool)
├── dkek_core.py           # EINZIGE Quelle der Wahrheit: DKEK Wrap/Unwrap/Status (sc-hsm-tool)
├── objects_core.py        # EINZIGE Quelle der Wahrheit: Objekt-Lifecycle (keys.*)
├── apdu_core.py           # EINZIGE Quelle der Wahrheit: Vendor-APDUs (RTC, Dynamic Options)
├── backup_core.py          # Backup/Restore/Drill (age + ssss als Subprozesse)
├── age_bech32.py            # Bech32 encode/decode (BIP-173) für age-Identity-Strings
└── backup_index.py        # scannt Backup-Verzeichnisse für `backup list`

gui/  (Schritt 5: Skeleton; alle 7 Tabs ausgebaut, siehe GUI-Stand)
├── app.py                # QApplication-Einstieg (Dark-Default), `pico-hsm-gui`
├── config.py             # qconfig-Einstellungen (~/.pico_hsm/gui.json)
├── main_window.py        # FluentWindow, Sidebar (1 Bereich pro CLI-Gruppe)
├── workers.py            # QRunnable-Wrapper (lange Ops nie im UI-Thread)
├── session_helpers.py    # MessageBox-Dialoge + Session-Opener (Qt-Pendant zu pkcs11_helpers.py)
└── tabs/                 # alle 7 Tabs ausgebaut (status/setup/pin/dkek/keys/backup/firmware)
```

### GUI-Stand (Phase-1-GUI komplett)

Start via `pico-hsm-gui` (PySide6 + QFluentWidgets, Dark-Default mit
Umschalter in der Sidebar). Die Sidebar spiegelt die CLI-Gruppen (Status,
Setup, PIN, DKEK, Schlüssel, Backup, Firmware) — Status-, Setup-,
PIN-, DKEK-, Schlüssel- und Backup-Tab sind ausgebaut (Backup inkl.
Split/Restore/Drill/Liste), der Firmware-Tab ebenfalls (Preflight,
geführtes Flashen mit TOTP-Dialog, Audit-Tabelle) — **alle 7 Tabs
ausgebaut, Phase-1-GUI komplett**. Hinweis: QFluentWidgets steht unter GPLv3
(bei Weitergabe der App beachten).

### Sicherheitsmodell in Kürze

- **Firmware-Flash** (`flash_core.py`): Board-Fingerprint-Check gegen OTP
  → Signaturprüfung → Anti-Rollback-Check → optionale TOTP-Autorisierung
  → Flash → manipulationssicheres Hash-Chain-Audit-Log. TOTP-Secret lebt
  nur lokal (`~/.pico_hsm/totp_secret.txt`), der 6-stellige Code kommt
  manuell von einem physisch getrennten Gerät.
- **Backup** (`backup_core.py`): age-Verschlüsselung + Shamir-Split des
  rohen 32-Byte-Identity-Keys. Ciphertext-Prüfsumme vor, Plaintext-
  Prüfsumme nach dem Restore geprüft — Manipulation oder falsche Shares
  werden erkannt, bevor eine unvertrauenswürdige Datei zurückgegeben wird.
- **Gateway-/Session-Konflikt**: rein informativer TCP-Check gegen das
  HSM-Gateway plus gezielte Behandlung der PKCS#11-Fehlerklassen, die
  beim tatsächlichen Öffnen einer schreibenden Session auf einen
  belegten Reader hindeuten (aktuell `keys delete`, künftig alle
  `objects_core.py`-Operationen — siehe `pkcs11_session.py`). Die
  subprozessbasierten Befehle `pin`/`init` öffnen keine eigene
  PKCS#11-Session; dort gibt es stattdessen nur den reinen Vorab-Hinweis
  (`warn_if_gateway_reachable`) — ob wirklich ein Konflikt vorliegt,
  zeigt dort letztlich der Exit-Code des externen Tools (`sc-hsm-tool`/
  `pkcs11-tool`).

---

## 5. Test- und Verifizierungsstand

Unterschieden nach **empirisch gegen echte Software getestet** und
**gegen offizielle Dokumentation verifiziert, aber nicht hardwaregetestet**.

### ✅ Empirisch end-to-end getestet (echte Binaries, nicht nur Mocks)

**Backup (`age`/`ssss` real installiert):**
- Bech32-Decoder gegen echten `age-keygen`-Output verifiziert (Encode→Decode-Roundtrip identisch)
- Kompletter Workflow: Datei verschlüsseln → Identity splitten → 3-von-5-Shares kombinieren → entschlüsseln → Ergebnis bitgenau identisch zum Original
- Negativtest: manipulierter Ciphertext wird **vor** dem Entschlüsseln abgelehnt
- Negativtest: zu wenige Shares werden abgelehnt
- Kompletter CLI-Pfad `backup split` → `backup list` → `backup restore` end-to-end mit `diff`-Bestätigung

**Firmware (`picotool` aus dem Quellcode gebaut, echtes RP2350-Testbinary):**
- `pico-sdk` + `picotool` v2.3.0 aus GitHub geklont und kompiliert
- `pico-examples/blink` für RP2350 gebaut, mit `picotool seal --sign --major 1 --minor 0 --rollback 3` signiert
- Dabei zwei reale Bugs im ursprünglichen Entwurf gefunden und korrigiert:
  1. `picotool info` hat **kein** `--json` (existiert nicht) — Version/Rollback werden jetzt aus der Klartextzeile `version: MAJOR.MINOR`/`rollback version: N` geparst
  2. `picotool verify <datei>` prüft **keine** Datei-Signatur, sondern vergleicht ein Gerät gegen eine Datei — schlägt ohne Gerät immer fehl. Die echte Prüfung läuft über `info -a` → Zeile `signature: verified/incorrect`. **picotool gibt bei ungültiger Signatur trotzdem Exit-Code 0 zurück** — Textparsing ist zwingend, der Exit-Code allein reicht nicht
- Test 1: gültige signierte Datei → akzeptiert, Version/Rollback korrekt gelesen
- Test 2: echte Byte-Manipulation in einem UF2-Datenblock → korrekt als `incorrect` erkannt
- Test 3: unsignierte Datei ohne Version → korrekt mit `FlashError` abgelehnt
- Kompletter `run_preflight()`-Workflow gegen die echte, signierte Datei erfolgreich

### ⚠️ Gegen offizielle Dokumentation verifiziert, nicht hardwaregetestet

- `dkek`-Befehle: Syntax 1:1 aus `polhenarejos/pico-hsm/doc/backup-and-restore.md` übernommen
- `pin`-Befehle: Syntax aus offizieller `pkcs11-tool`-Manpage und OpenSC-Wiki (SmartCardHSM-Seite)
- `keys.py`: Attribute/Methoden 1:1 aus der offiziellen `python-pkcs11`-API-Referenz (readthedocs)
- `init token`: Syntax aus offizieller `sc-hsm-tool`-Manpage

### ❌ Bekannte offene Punkte (brauchen echtes Pico-HSM-Board)

- Das exakte `BOOTKEY0_N`-OTP-Feldnamensschema in `get_burned_key_fingerprint()`
  ist mit simulierter picotool-Ausgabe getestet (Erfolg/kein-Match/
  unvollständig-Fälle), aber der tatsächliche Feldname selbst konnte
  ohne physischen RP2350-Chip mit gebranntem Secure-Boot-Key nicht
  verifiziert werden. Bei Abweichung bricht die Funktion **kontrolliert
  mit klarer Fehlermeldung ab**, statt einen falschen Fingerprint
  zurückzugeben.
- `pin change/unblock/status`, `dkek create-share/import-share/wrap-key/
  unwrap-key/status`, `init token`, `keys list/delete`: Syntax verifiziert,
  aber nie gegen ein reales, angeschlossenes Pico-HSM-Token ausgeführt.
- `keys list`: der numerische `Key ref` (nötig für `dkek wrap-key`) wird
  aktuell nicht aus `python-pkcs11` ausgelesen — dafür zusätzlich
  `pkcs15-tool -D` nötig (noch nicht integriert).
- `backup_index.py`: Manifest-Feldnamen (`created_at`, `threshold` etc.)
  sind konsistent mit dem, was `backup_core.py` selbst schreibt — aber
  falls extern erzeugte Backups einer älteren, nicht mehr vorhandenen
  Skript-Generation eingelesen werden sollen, sind deren Feldnamen nicht
  geprüft.

**Empfehlung vor Produktiveinsatz:** kompletten Durchlauf (`init` →
`dkek` → `pin change` → `keys` → `backup` → `firmware preflight`) an
einem Ersatzboard durchspielen, bevor es an einem Produktivgerät läuft
(siehe Gesamtprojekt-Doku `docs/15-real-hardware-validation-checklist.md`).