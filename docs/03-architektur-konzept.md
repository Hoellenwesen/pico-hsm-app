# Pico HSM App — Architektur-/Modulkonzept (Neuaufbau)

Stand: Analyse des aktuellen Codes in `Hoellenwesen/pico-hsm`,
`Hoellenwesen/pico-keys-sdk`, `Hoellenwesen/pico-hsm-app`,
`Hoellenwesen/pico-hsm-api-connector` (jeweils `HEAD`, per Clone geprüft,
nicht nur READMEs). Dieses Dokument war die Planungsgrundlage vor der
Implementierung — Schritte 1–6 (CLI, alle 7 GUI-Tabs) sowie eine
Nachbereitung (Intensivprüfung + Fixes, Punkt 7) sind umgesetzt
(344 Tests, Stand dieser Revision, alle ohne Hardware lauffähig über
Mocks); offen sind nur noch echte Hardware-Verifikation (§12) und
Phase 2 (Anhang A). Aktuell: 344 Tests.

## 1. Ziel

`pico-hsm-app` wird von reinem CLI-Tool zu einer App mit **primärer,
moderner GUI und optionalem CLI-Modus**. Beide Oberflächen greifen auf
dieselbe, UI-unabhängige Kernlogik zu — keine Logikduplikation zwischen
GUI und CLI.

**Phase 1 (aktueller Scope, dieses Dokument):** ausschließlich
Management-Funktionen — Token-Init, PIN/DKEK, Keys/Zertifikate/
Datenobjekte, Setup (OTP/Dynamic Options), Backup/Restore,
Firmware-Update. Deckt damit den vollen Management-Funktionsumfang der
aktuellen Firmware ab.

**Phase 2 (zurückgestellt):** ein Diagnose-Modus über das Gateway
(sign/verify/encrypt/decrypt/derive gegen dedizierte Test-Keys, kein
direkter PKCS#11-Zugriff auf Live-Keys). Das Design dafür ist bereits
ausgearbeitet und in Anhang A festgehalten, wird aber aktuell nicht
gebaut — siehe Nicht-Ziele.

## 2. Nicht-Ziele (bewusst ausgeklammert)

- **Public Key Authentication (PKA)**: Zwei-Geräte-Schema, in der Praxis
  ein Sonderfall (Custodian-Verteilung), nicht Teil dieses Neuaufbaus.
- **SCS3-artiger PKCS12-/WKY-Import**: hängt am proprietären
  CardContact-Java-Tool, es gibt kein PKCS#11-Äquivalent dafür laut
  `doc/scs3.md` (SmartCard-HSM-Doku, Fremd-Repo). Nicht reimplementiert.
- **Sign/Verify/Encrypt/Decrypt/Derive auf Produktivschlüsseln**: bleibt
  exklusiv beim Gateway, unverändert. **Der Diagnose-Modus über das
  Gateway ist vorerst zurückgestellt** (Phase 2) — diese App-Version
  deckt ausschließlich Management-Funktionen ab. Das fertige Design
  (Client-Modul, Protokoll-Mapping, Contract-Test-Ansatz) bleibt in
  Anhang A dokumentiert, damit es bei Bedarf ohne erneute Analyse
  aufgegriffen werden kann.

## 3. Architekturüberblick

```
                    ┌─────────────────────────────┐
                    │        pico-hsm-app          │
                    │                              │
   ┌───────────┐    │  ┌────────┐      ┌────────┐  │
   │  CLI       │◄──┼──┤ cli/   │      │ gui/   │──┼──►  ┌───────────┐
   │ (Terminal) │    │  └───┬────┘      └───┬────┘  │    │ Qt-Fenster │
   └───────────┘    │      │               │       │    └───────────┘
                    │      └───────┬───────┘       │
                    │              ▼               │
                    │      pico_hsm_tools/          │
                    │   (Core, UI-unabhängig,       │
                    │    EINZIGE Quelle der          │
                    │    Wahrheit pro Bereich)       │
                    └──────┬──────────────┬─────────┘
                           │              │
              PKCS#11 (direkt,    TCP-Connect (nur Erreichbar-
              Management-Ops)     keits-Check, informativ,
                           │       bereits aktiv seit Schritt 1)
                           ▼              │
                    ┌────────────┐         ▼
                    │ Pico HSM    │  ┌────────────────────┐
                    │ (USB/PKCS11)│  │ pico-hsm-api-       │
                    └────────────┘  │ connector (Gateway) │
                                     └──────────┬──────────┘
                                                 │ PKCS#11 (exklusiv,
                                                 │ sign/verify/en-/decrypt/derive)
                                                 ▼
                                          Pico HSM (dasselbe Gerät)
```

Der mTLS/JSON-Diagnose-Kanal (App → Gateway, authentifiziert, für
sign/verify/encrypt/decrypt/derive) ist bewusst NICHT in diesem
Diagramm — das ist Phase 2, siehe Anhang A. Einen
Erreichbarkeits-/Kollisions-Check gibt es nicht mehr (entfernt, da
App und Gateway auf getrennten Systemen laufen, siehe unten).

Wichtig: App und Gateway sind zwei unabhängige PKCS#11-Konsumenten
— die App macht Management (Keys anlegen/löschen,
PIN, DKEK, Backup, Firmware), das Gateway macht laufenden Crypto-Betrieb
für Netzwerk-Clients.

### Betriebsmodell

Es gibt genau ein Modell: **App und Gateway laufen auf getrennten
Systemen.** Die App läuft auf einem Arbeitsplatzrechner für
Ersteinrichtung und gelegentliche Verwaltung (HSM per USB), das
Gateway läuft dauerhaft auf einem anderen System. Weil der komplette
Zustand (PIN, Keys, DKEK-Shares, Zertifikate) auf dem Hardware-Token
selbst liegt, braucht es **keine Verbindung zwischen App und Gateway**
— beide reden nur direkt mit dem physisch angeschlossenen Gerät, nie
miteinander. Zu keinem Zeitpunkt sind zwei Konsumenten gleichzeitig
am Gerät. Einen Erreichbarkeits-/Kollisions-Check gibt es daher nicht
mehr (sauber entfernt): Schlägt das Öffnen einer schreibenden Session
mit einer Fehlerklasse fehl, die auf einen belegten Reader hindeutet,
meldet die App schlicht einen belegten Reader (anderer lokaler
Prozess).

## 4. Technologie-Entscheidungen (Rekap mit Begründung)

| Entscheidung | Begründung |
|---|---|
| **PySide6** statt NiceGUI/pywebview | Kein zusätzlicher lokal lauschender Webserver-Prozess nötig — für ein Tool, das PINs/SO-PINs/DKEK-Shares handhabt, unnötige Angriffsfläche. In-Process-Aufruf der Core-Module, keine IPC-/Serialisierungsgrenze. |
| **PySide6** statt Tauri | Kein zweiter Sprach-Stack (Rust+JS) nur für die UI-Schicht. Passt zur bestehenden Prämisse "nur Orchestrierung ist Python". |
| **QFluentWidgets** als Theming-Layer (getroffene Entscheidung, Schritt 5) | Fluent-Design-Komponenten statt QSS-Gefrickel, Sidebar-Navigation (`FluentWindow`) von Haus aus; `PySide6-Fluent-Widgets`-Paket (Import `qfluentwidgets`). **Lizenz-Hinweis:** GPLv3 — bei Weitergabe der App beachten. |
| **CLI bleibt `click`** | Bereits etabliert, keine Notwendigkeit für einen Wechsel. |
| **`pyscard` für Vendor-APDUs** (Dynamic Options) | Direkter PC/SC-Zugriff mit strukturierten Byte-Antworten statt `opensc-tool -s`-Textausgabe parsen — das Projekt hat mit dem `picotool`-Textparsing bereits zwei reale Bugs eingefangen (siehe README, Abschnitt 5); dieselbe Fehlerklasse hier von vornherein vermeiden. |
| **Python bleibt Sprache der App** (kein Wechsel zu Go/Rust/C#) | Flaschenhals ist immer der Subprozess/das HSM, nie die App-Logik — eine Systemsprache bringt keinen spürbaren Gewinn. Bereits getesteter Code (age-Roundtrips, gefixte `picotool`-Bugs) bliebe sonst ungenutzt. Vierte Projektsprache für dieselbe Rolle (Mensch bedient App, orchestriert vertrauenswürdige externe Binaries) ohne Sicherheits-/Perf-Gewinn. (Das ursprüngliche Restrisiko-Argument — Protokoll-Drift in einem künftigen `gateway_client.py` — betrifft jetzt nur noch Anhang A/Phase 2.) |

## 5. Zielstruktur

```
pico-hsm-app/
├── cli/                         # bestehend, CLI-Präsentation (click)
│   ├── main.py                  # ✅ erweitert (Flags: --serial/--reader)
│   ├── context.py               # ✅ erweitert (Token-Auswahl im CliContext: --serial/--reader)
│   ├── pkcs11_helpers.py        # ✅ angepasst (Schritt 1, Gateway-Check inzwischen entfernt)
│   └── commands/
│       ├── init.py / pin.py / dkek.py / backup.py / firmware.py   # ✅ Konsumenten-Anpassung (Schritt 1, ohne Gateway-Check)
│       ├── status.py            # ✅ erledigt (Schritt 1: device+audit; gateway-Befehl entfernt)
│       ├── setup.py             # ✅ erledigt (Schritt 3: dynamic-options get/set)
│       └── keys.py              # ✅ erledigt (Schritt 2: generate, generate-aes, object write/read/delete, random)
├── gui/                         # ✅ SKELETON (Schritt 5) + alle Tabs ausgebaut (Schritt 6)
│   ├── app.py                   # QApplication-Einstieg, Dark-Default (setTheme)
│   ├── config.py                # qconfig-Einstellungen (~/.pico_hsm/gui.json, Schritt 6a)
│   ├── main_window.py           # FluentWindow, Sidebar (1 Bereich pro CLI-Gruppe)
│   ├── session_helpers.py       # Qt-Pendant zu cli/pkcs11_helpers.py (MessageBox-Dialoge)
│   ├── workers.py                # QThread/QRunnable-Wrapper für lange Operationen
│   └── tabs/                    # ✅ alle 7 Tabs ausgebaut (Schritt 6a–6g)
│       ├── status_tab.py / setup_tab.py / pin_tab.py / dkek_tab.py
│       └── keys_tab.py / backup_tab.py / firmware_tab.py
├── pico_hsm_tools/              # Core, UI-unabhängig
│   ├── flash_core.py            # unverändert
│   ├── backup_core.py           # unverändert
│   ├── age_bech32.py            # unverändert
│   ├── backup_index.py          # unverändert
│   ├── pkcs11_session.py        # ✅ ÜBERARBEITET, erledigt (Schritt 1, §7.d)
│   ├── audit_log.py             # ✅ reines Audit-Log (Gateway-Teil entfernt)
│   ├── objects_core.py          # ✅ NEU, erledigt (Schritt 2, §7.a) — konsolidiert auch die bisherige Inline-Logik aus cli/commands/keys.py
│   ├── apdu_core.py             # ✅ NEU, erledigt (Schritt 3, §7.b)
│   ├── pin_core.py              # ✅ NEU, erledigt (Schritt 6c) — konsolidiert PIN-Logik aus cli/commands/pin.py
│   └── dkek_core.py             # ✅ NEU, erledigt (Schritt 6d) — konsolidiert Wrap/Unwrap/Status aus cli/commands/dkek.py
├── pyproject.toml               # ✅ + pyscard (Schritt 3), + PySide6/QFluentWidgets (Schritt 5);
#                                EINZIGE Dependency-Quelle (requirements.txt gestrichen — war redundant)
├── tools/                       # ✅ NEU (Hardware-Validierung): hw_survey, hw_hold_session, hw_second_accessor
└── docs/                        # ✅ NEU: 15-real-hardware-validation-checklist.md
```

(`diagnose.py`, `diagnose_tab.py` und `gateway_client.py` sind Teil des
in Anhang A dokumentierten, zurückgestellten Phase-2-Designs — nicht
Teil dieser Struktur.)

Neuer Skript-Einstiegspunkt in `pyproject.toml` (beide umgesetzt):
```toml
[project.scripts]
pico-hsm-cli = "cli.main:cli"
pico-hsm-gui = "gui.app:main"  # ✅ erledigt (Schritt 5)
```

## 6. Funktions-/Command-Matrix

| Firmware-Funktion | Referenz | Status heute | Neues Kommando | Core-Modul | GUI-Tab |
|---|---|---|---|---|---|
| Token-Init | `usage.md` | ✅ vorhanden | `init token` | `init_core.py` | `wizard_tab.py` |
| Keypair-Erzeugung RSA/EC | `usage.md` | ✅ **erledigt (Schritt 2)** | `keys generate` | `objects_core.py` | `keys_tab.py` |
| AES-Key-Erzeugung | `aes.md` | ✅ **erledigt (Schritt 2)** | `keys generate-aes` | `objects_core.py` | `keys_tab.py` |
| Objekt-Liste/-Löschung | `usage.md` | ✅ **konsolidiert (Schritt 2)** — vorher inline in `keys.py`, jetzt `objects_core.py` | `keys list` / `keys delete` | `objects_core.py` | `keys_tab.py` |
| Zertifikat/Data-Object schreiben/lesen/löschen | `store_data.md` | ✅ **erledigt (Schritt 2)** | `keys write-object` / `read-object` | `objects_core.py` | `keys_tab.py` |
| Zufallszahlen | `usage.md` | ✅ **erledigt (Schritt 2)** | `keys random` | `objects_core.py` | `keys_tab.py` |
| PIN/SO-PIN-Verwaltung | `usage.md` | ✅ vorhanden | `pin ...` | `pin_core.py` | `pin_tab.py` |
| DKEK-Shares, Wrap/Unwrap | `backup-and-restore.md` | ✅ vorhanden | `dkek ...` | `dkek_core.py` | `dkek_tab.py` |
| RTC get/set | `extra_command.md` | ❌ **entfernt (Firmware v6.6 implementiert P1=0x0A nicht — Karte antwortet 6A86, quellverifiziert)** | — | — | — |
| Dynamic Options (Press-to-Confirm, Key-Usage-Counter) | `extra_command.md` | ✅ **erledigt (Schritt 3)** | `setup dynamic-options get/set` | `apdu_core.py` | `setup_tab.py` |
| OTP-Anzeige (Secure Boot etc., read-only) | Projekt-intern | ✅ vorhanden | `setup show` | `flash_core.py` | `setup_tab.py` |
| Backup/Restore/Drill (age, Empfänger-Modus) | Projekt-intern | ✅ vorhanden | `backup ...` | `backup_core.py` | `backup_tab.py` |
| Firmware-Update | Projekt-intern | ✅ vorhanden | `firmware ...` | `flash_core.py` | `firmware_tab.py` |
| Sign/Verify/Encrypt/Decrypt/Derive | `sign-verify.md`, `asymmetric-ciphering.md`, `aes.md` | bewusst exkludiert; Diagnose-Modus **zurückgestellt (Phase 2)** | — (Anhang A) | — (Anhang A) | — (Anhang A) |

## 7. Neue/angepasste Core-Module im Detail

### a) `objects_core.py` (neu)

Konsolidiert Objekt-Lifecycle-Operationen, die heute teils fehlen, teils
inline in `cli/commands/keys.py` stecken (Bruch mit dem sonst
durchgehaltenen Prinzip "eine Quelle der Wahrheit pro Bereich" —
`flash_core.py`/`backup_core.py` haben das, `keys.py` bisher nicht).

Geplante Funktionen (Signaturen grob, Details in der Implementierung):
- `generate_keypair(session, key_type, id, label) -> ObjectInfo` — RSA
  (1024/2048/4096) und die neun EC-Kurven aus `usage.md`.
  **Warnhinweis fest im Rückgabe-/Progress-Objekt verankern**: RSA-2048
  braucht am Board ca. 2:45 Min., RSA-4096 ca. 15:00 Min. (Doku-Werte
  >20s/>20min widerlegt bzw. überholt) — UI-Schicht MUSS das vorab
  anzeigen, nicht erst bei Timeout entdecken.
- `generate_aes_key(session, bits, id, label) -> ObjectInfo` — 128/192/256 Bit.
- `list_objects(session) -> list[ObjectInfo]` — migrierte Logik aus
  `cli/commands/keys.py::list_cmd`.
- `delete_object(session, label) -> bool` — migrierte Logik aus `delete_cmd`.
- `write_data_object(session, data: bytes, id, label, private: bool) -> ObjectInfo`
  — max. 4096 Byte (Firmware-Limit), `private=True` → PIN-geschützt.
- `read_data_object(session, label) -> bytes`
- `generate_random(session, num_bytes: int) -> bytes` — Firmware-Limit 1024 Byte.

Offener Punkt: `usage.md`/`store_data.md` demonstrieren alles über
`pkcs11-tool`/`sc-hsm-tool` als externe Programme, nicht über
`python-pkcs11`. Ob `python-pkcs11` (`session.generate_keypair`,
`session.create_object` für Data-Objekte) exakt dieselben
Objektattribute erzeugt, ist **hardwareverifiziert** (docs/15 Phase 2.2+2.3, hw-logs/07-10) — Rest s.u.
den bereits als ⚠️/❌ markierten Punkten im bestehenden README.

### b) `apdu_core.py` (neu)

Vendor-APDUs gemäß `extra_command.md` (`CLA=80 INS=64`), korrigiert
gegen `pico-hsm/src/hsm/cmd_extras.c` + `sc_hsm.h`:
- `get_dynamic_options(connection) -> DynamicOptions` (Bitfeld: Press-to-Confirm, Key-Usage-Counter)
- `set_dynamic_options(connection, options: DynamicOptions) -> None`

Quellverifizierte Fakten (Ersatzboard-Messungen + Firmware-Code):
GET ist `80 64 06 00 02` mit 2-Byte-uint16-BE-Antwort, Bits im
HIGH-Byte (`0x0100`/`0x0200`); SET-Bytes unverändert. `61 XX`/`6C XX`
werden behandelt. ALLE Vendor-Kommandos brauchen vorherigen PIN-Login
(`isUserAuthenticated`, sonst SW=6982) — Setup-Reads setzen ihn voraus
(siehe Hinweis in CLI/GUI). RTC-Datetime (P1=0x0A) ENTFERNT:
`CMD_DATETIME` ist definiert, wird aber nirgends behandelt (Karte:
`6A86`) — Doku-Fiktion in v6.6.

Implementierung über `pyscard` (`smartcard.CardConnection`), nicht über
`opensc-tool -s` + Textparsing.

**Getroffene Entscheidung (Schritt 3, umgesetzt):** APDU-Kommandos
laufen sequenziell und NIE parallel zu einer offenen PKCS#11-Session —
`setup dynamic-options` öffnet selbst keine PKCS#11-Session (nur der
vorherige Login läuft separat), sodass die Invariante strukturell gilt;
ein Reader-belegt-Fall wird mit klarem Hinweis (anderer lokaler Prozess) abgebrochen. Sharing-Mode per Hardware verifiziert (docs/15 Phase 2.8, hw-logs/17).

### c) `gateway_client.py` — Diagnose-Modus (zurückgestellt, Phase 2)

Design fertig ausgearbeitet, aber nicht Teil des aktuellen Scopes —
vollständig dokumentiert in **Anhang A** am Ende dieses Dokuments.

### d) `pkcs11_session.py` — Konflikt-Check (vereinfacht, Stand heute)

Historie: `check_daemon_running()`/`DaemonState`/`DAEMON_SOCKET_PATH`
(Unix-Socket-Check) entfielen mit dem Daemon; danach gab es zweistufig
weich (TCP-Erreichbarkeit) + hart (echter Session-Fehler). Der weiche
Check ist inzwischen ebenfalls entfernt — App und Gateway laufen auf
getrennten Systemen (Betriebsmodell in §3), ein Kollisions-Check wäre
bedeutungslos.

Stand heute: `exclusive_session()` öffnet direkt; schlägt das mit einer
Fehlerklasse fehl, die auf einen belegten Reader hindeutet
(`_LIKELY_CONFLICT_ERRORS`), wird das als `SessionConflictError` neu
geworfen — reiner Belegt-Text (anderer lokaler Prozess), ohne
Gateway-Kontext. Verifiziert per Hardware (docs/15 Phase 2.8,
hw-logs/17: kein OS-Lock, Sicherheitsnetz bleibt).

### e) `audit_log.py` (vormals `gateway_status.py`, vormals `daemon_status.py`)

Nur noch Firmware-Update-Audit-Log der App (`AuditEntry`,
`tail_flash_audit_log`, `audit_chain_intact`) — betrifft
ausschließlich die App, nicht das Gateway-eigene Audit-Log
(`src/audit.rs`, separates System). `StatusSnapshot`,
`GatewayState` und `check_gateway_reachable` sind mit dem
Kollisions-Check entfallen.

## 8. GUI-Architektur

- **1 Tab pro CLI-Kommandogruppe** (`status`, `setup`, `pin`, `dkek`,
  `keys`, `backup`, `firmware`) — direkte Entsprechung, damit
  Doku/Mental-Model für beide Oberflächen identisch bleibt.
- **Threading**: jeder Aufruf, der einen Subprozess startet oder eine
  potenziell lange PKCS#11-Operation ist (v.a. `keys generate` bei
  RSA-2048/4096), läuft in einem `QRunnable` über `QThreadPool`, Ergebnis
  per Qt-Signal zurück in den Main-Thread. Blockierender Direktaufruf im
  UI-Thread ist für keinen Fall zulässig, der >1s dauern kann.
- **PIN-Eingabe**: `QLineEdit` mit `EchoMode.Password`, kein Logging,
  kein Klartext in Exception-Messages, Variable nach Gebrauch
  überschreiben (Python-Strings sind immutable — vollständige
  Zeroization ist damit nicht garantierbar, das ist eine inhärente
  Grenze, keine Lücke speziell dieser App).
- **Konflikt-/Fehlerdialoge**: `gui/session_helpers.py` spiegelt
  `cli/pkcs11_helpers.py` — bei `SessionConflictError` erscheint ein
  `QMessageBox` mit derselben Meldung wie im CLI-Fall. Kein "Trotzdem
  fortfahren"-Button (siehe §7.d/Schritt 1: der Fehler ist zu dem
  Zeitpunkt bereits real aufgetreten, ein Retry ändert daran nichts) —
  nur "OK"/"Erneut versuchen".
- **Theming**: qt-material oder QFluentWidgets (Entscheidung zwischen
  beiden ist reine Geschmacksfrage, keine Architekturfrage — kann bei
  GUI-Skeleton-Umsetzung fallen).

## 9. Sicherheitsbetrachtung (neue Bereiche)

| Bereich | Risiko | Gegenmaßnahme |
|---|---|---|
| `objects_core.py` Keypair-Gen | Schwacher/falscher Mechanismus versehentlich wählbar | Nur die in `usage.md` gelisteten Kombinationen als Auswahl anbieten, kein Freitext-Mechanismus-Feld |
| `apdu_core.py` Dynamic Options | Press-to-Confirm/Key-Usage-Counter versehentlich deaktiviert (Security-Downgrade) | GUI: explizite Bestätigung beim **Deaktivieren** von Security-Features, nicht nur beim Aktivieren |
| PIN-Eingabe GUI | Clipboard-/Screenshot-Leakage | `EchoMode.Password`, kein Copy-Paste-Button, kein Autofill |
| Datenobjekte (`write_data_object`) | Versehentliches Schreiben ohne `--private`/PIN-Schutz (Klartext auslesbar ohne PIN) | GUI: `private` ist Default `True`, explizites Opt-out nötig, nicht umgekehrt |
| Konflikt-Check (§7.d) | Belegt-Meldung ohne Ursache | Schlägt das Öffnen mit einer Fehlerklasse fehl, die auf einen belegten Reader hindeutet, meldet die App einen belegten Reader (anderer lokaler Prozess) — kein Erreichbarkeits-Check, keine Freigabe-Logik nötig |

## 10. Konfiguration

**Stand heute:** Es gibt keine Gateway-Flags und keine Gateway-Config
mehr — `--gateway-host`/`--gateway-port`, `status gateway` und der
Erreichbarkeits-Check sind entfernt (App und Gateway laufen auf
getrennten Systemen, siehe §3).

Eine Config-Datei mit mTLS-Zertifikatspfaden
(`~/.pico_hsm/gateway_client.toml`) wäre ausschließlich für den
Diagnose-Modus nötig — Details dazu in Anhang A, aktuell nicht
umgesetzt.

## 11. Migrations-/Breaking-Changes-Hinweise

- `pico_hsm_tools.daemon_status` → `pico_hsm_tools.gateway_status`
  → `pico_hsm_tools.audit_log` (Import-Pfad ändert sich, betrifft nur
  internen Code, keine CLI-Flags).
- `DaemonState` → `GatewayState` (entfallen), `check_daemon_running()`
  → `check_gateway_reachable(host, port)` (entfernt, kein Ersatz —
  getrennte Systeme brauchen keinen Kollisions-Check).
- `cli status daemon` → `cli status gateway` → entfernt
  (`status all` = `device` + `audit`).
- `cli/commands/keys.py`: `list`/`delete` rufen künftig
  `objects_core.py` statt inline `python-pkcs11`-Code auf — Verhalten
  nach außen unverändert, nur interne Konsolidierung.
- README-Verweis auf "pqvault" entfällt (laut Projekt-Historie retired
  — die App gehört zum PicoHSM-Projekt).

## 12. Offene Punkte vor Umsetzung (ehrlich, analog zum bestehenden README-Stil)

- ✅ `python-pkcs11`-API für Keypair-/Data-Object-Erzeugung am Board verifiziert (docs/15 Phase 2.2+2.3, hw-logs/07-12).
- ⚠️ Sharing-Mode-Frage PC/SC (§7.b) — die Strategie-Entscheidung
  ist getroffen und umgesetzt (`apdu_core.py` läuft sequenziell/
  exklusiv, nie parallel zu einer offenen PKCS#11-Session).
  Board-Messung (`hw-logs/17-...`, Zwei-Prozess-Test): parallele
  Sessions + APDU daneben funktionieren STÖRUNGSFREI — PKCS#11 kennt
  keinen OS-exklusiven Session-Lock (rw+Login ist Konvention), die
  sequenziell/exklusiv-Regel bleibt als vorsichtiger Default.
  Echte Treiber-Level-Konflikte damit weiter ungeprüft, kein Blocker.
- ✅ Dynamic-Options-GET/SET gegen Firmware-Quellen korrigiert und
  am Board verifiziert: GET ist `80 64 06 00 02` (2-Byte-uint16-BE,
  Bits im High-Byte — `cmd_extras.c`/`sc_hsm.h`), Messung nach Login:
  `OK (maske=0x00)` auf frischem Board. Login-Pflicht bestätigt
  (Setup-Reads setzen PIN-Login voraus; Login persistiert
  kartenweit über Prozesse — kein PIN-Parameter nötig). Voll-Roundtrip
  `0x00->0x02->0x03->0x00` am Board verifiziert (`hw-logs/06-...`).
  Offen: P2C-Tastendruck-Enforcement — trotz aktivem P2C wurde KEIN
  Tastendruck verlangt (Build ohne ENABLE_EMULATION verifiziert);
  ob/wie das Gate greift, braucht einen Timing-Test. Der Doc-Text enthält außerdem einen Copy-Paste-Fehler
  beim Counter-SET (`Sending: ... 01 01` statt `... 01 02` laut
  opensc-tool-String `806406000102`); implementiert ist die
  dekodierte Bytefolge, siehe `apdu_core`-Moduldoc.
- ⚠️ Exakte Exception-Klasse für "Gerät belegt" in `python-pkcs11`
  (§7.d): per Zwei-Prozess-Test NICHT auslösbar (parallele Sessions
  funktionieren) — `SessionConflictError` bleibt als ungetestetes
  Sicherheitsnetz (Mock-Tests) für echte Belegt-Fälle. Ohne
  blockierenden Zugriffspfad nicht weiter verifizierbar.
- ⚠️ RSA-4096-Keygen-Timeout-Handling: PKCS#11-Sessions haben i.d.R.
  keinen sinnvollen "Abbrechen"-Pfad während einer laufenden
  Schlüsselerzeugung im Secure Element — die GUI zeigt Warnung und
  Fortschritt (Keys-Tab), aber ein Abbrechen-Button wäre vermutlich nur
  kosmetisch (Session bliebe belegt). An echter Hardware prüfen.
- ⚠️ Nativer PC/SC-Fehler statt Exception: Auf einer Maschine ohne
  funktionierenden Smartcard-Dienst fault `SCardListReaders` (pyscard)
  nativ (Windows-Fehler 0x8010002e, in der Suite beobachtet), statt
  fangbar zu scheitern — betrifft real Vendor-APDU-Zugriffe (z.B.
  `setup dynamic-options`) auf solchen Maschinen. Abfangen ist aus
  Python nicht möglich (SEH-Fault, keine Python-Exception); Workaround
  wäre ein vorgeschalteter Dienst-Verfügbarkeitscheck, Aufwand/Nutzen
  vor Umsetzung abwägen (Entscheidung: dokumentiert reicht).

## 13. Umsetzungsreihenfolge (Vorschlag)

1. ✅ **Erledigt.** `pkcs11_session.py` überarbeitet (§7.d) + `audit_log.py` (vormals `gateway_status.py`, §7.e), inkl. Anpassung aller direkten Konsumenten
   (`context.py`, `main.py`, `pkcs11_helpers.py`, `init.py`, `pin.py`,
   `status.py`) und der begleitenden README-Bereinigung
   (pqvault-/Daemon-Referenzen entfernt).
2. ✅ **Erledigt.** `objects_core.py` (§7.a) inkl. Migration der
   bestehenden `keys list`/`keys delete`-Logik, neue Kommandos
   `keys generate`/`generate-aes`/`write-object`/`read-object`/`random`.
   Dabei einen realen Namenskonflikt gefunden und gefixt: die
   Brainpool-Kurvennamen aus `usage.md` ("brainpoolP256r1") matchen
   nicht die von `asn1crypto`/`python-pkcs11` erwarteten Namen
   ("brainpoolp256r1") — als Regressionstest in
   `tests/test_objects_core.py` festgehalten (28 Tests, alle ohne
   Hardware lauffähig über Mocks).
3. ✅ **Erledigt (revidiert nach Hardware-Befund).** `apdu_core.py`
   (§7.b): Dynamic Options über `pyscard`, inkl. CLI-Kommandos
   `setup dynamic-options get/set` (sequenziell/exklusiver
   Reader-Zugriff als getroffene Entscheidung). RTC-Datetime
   ENTFERNT (P1=0x0A existiert in Firmware v6.6 nicht — Karte: 6A86,
   quellverifiziert). Dynamic-Options-Mapping korrigiert (2-Byte-GET,
   High-Byte-Bits, 61xx/6Cxx-Behandlung) nach Firmware-Quellen +
Board-Messungen (6982 ohne Login, 6101 mit Login); PIN-Login-
Voraussetzung dokumentiert. Voll-Roundtrip mit gesetzten Bits
verifiziert (docs/15 Phase 2.1, hw-logs/06: 0x00->0x02->0x03->0x00).
4. ✅ **Erledigt (verifiziert).** CLI-Integration aller neuen
   Kommandos der Schritte 1–3: alle Kommandos registriert
   (`status gateway`, `keys generate`/`generate-aes`/`write-object`/
   `read-object`/`random`, `setup dynamic-options get/set`),
   README-Referenz für die fünf neuen `keys`-Kommandos ergänzt,
   `status all` emittiert im `--json`-Modus ein einzelnes Dokument,
   `keys import` hält die `ctx`-Konvention ein. 5 CLI-Regressionstests
   in `tests/test_cli_wiring.py` (71 Tests gesamt, ohne Hardware).
   (`setup datetime get/set` aus Punkt 3 später entfernt — siehe dort.)
5. ✅ **Erledigt (verifiziert).** GUI-Skeleton: `gui/app.py`
   (Dark-Default), `main_window.py` (FluentWindow, Sidebar mit 7
   Bereichen in CLI-Gruppen-Reihenfolge, Theme-Umschalter),
   `workers.py` (QRunnable + Ergebnis/Fehler-Signale),
   `session_helpers.py` (MessageBox-Dialoge), 7 Tab-Platzhalter mit
    deutschem Hinweistext (in Schritt 6 alle ausgebaut, siehe Punkt 6).
    14 GUI-Tests in `tests/test_gui_skeleton.py`
   (pytest-qt, offscreen — 85 Tests gesamt). Dabei zwei reale
   Library-Fallen dokumentiert und umschifft: `setCurrentItem` schaltet
   keine Seite um (nur Selektionsstatus — Wechsel nur per
   Nav-Klick/`clicked`-Signal), `activeModalWidget()` meldet den
   QFluentWidgets-Dialog offscreen nicht (Tests suchen per
   `findChildren`). Dependencies: `PySide6`, `PySide6-Fluent-Widgets`
   (GPLv3 beachten), dev: `pytest-qt`.
6. GUI-Tabs, einer nach dem anderen, in derselben Reihenfolge wie die
   CLI-Kommandogruppen oben. Begonnen (Schritt 6a, Muster-Tab):
   `status`-Tab ausgebaut (Gerät/Gateway/Audit, Config-Datei
   `~/.pico_hsm/gui.json`, Button- + Tab-Wechsel-Refresh, Audit-Tabelle,
   inline InfoBars) — 9 Tests in `tests/test_gui_status_tab.py`.
   Begonnen (Schritt 6b, zweiter Tab): `setup`-Tab ausgebaut
   (OTP-Anzeige via konsolidiertem `flash_core.read_otp_field`,
   RTC-Datetime mit DateTimeEdit + Jetzt-Button, Dynamic Options mit
   SwitchButtons; Confirm-Dialoge für Schreiben, verschärft bei
   P2C-Deaktivierung) — 11 Tests in `tests/test_gui_setup_tab.py`
   (105 Tests gesamt).
   Begonnen (Schritt 6c, dritter Tab): `pin`-Tab ausgebaut (Ändern,
   Entsperren, Status via neuem `pico_hsm_tools/pin_core.py` —
   pkcs11-tool-Subprozesse, python-pkcs11 hat keine PIN-API; strikt
   verdeckte Felder ohne Auge-Button, Felder nach Erfolg geleert,
   Warn-InfoBar bei kritischen Flags, Kein-Leak-Vertrag) — 11 Tests in
   `tests/test_gui_pin_tab.py` (116 Tests gesamt). Begonnen (Schritt 6d,
   vierter Tab): `dkek`-Tab ausgebaut (Status-Rohtext, Wrap-Export und
   Unwrap-Import via neuem `pico_hsm_tools/dkek_core.py` — sc-hsm-tool;
   Create/Import-Share bleiben CLI-only, da interaktives Terminal nötig;
   Datei-Dialoge, PIN-Felder strikt verdeckt/geleert, Confirm-Dialoge) —
   11 Tests in `tests/test_gui_dkek_tab.py` (127 Tests gesamt).
   Begonnen (Schritt 6e, fünfter Tab): `keys`-Tab ausgebaut (alle acht
   Bereiche: Liste/Löschen/Import-Hinweis/RSA/EC/AES/Schreiben/Lesen/
   Zufall; ein PIN-Feld pro Tab mit Leerung bei hideEvent;
   RSA-Warnlabel vorab + Fortschrittsanzeige; Session-Opener in
   `gui/session_helpers.py`, Tab-Sprung via `MainWindow.show_tab`) —
   18 Tests in `tests/test_gui_keys_tab.py` (145 Tests gesamt).
   Begonnen (Schritt 6f, sechster Tab): `backup`-Tab ausgebaut (alle
   vier Bereiche: Splitten mit 3-von-5-Default, Wiederherstellen mit
   Share-Parsing + Feld-Leerung, Selbsttest + echter Drill, Backup-Liste
   mit Leftover-Warnung; kein Core-Umbau nötig) — 12 Tests in
   `tests/test_gui_backup_tab.py` (157 Tests gesamt). Begonnen
   (Schritt 6g, siebter und letzter Tab): `firmware`-Tab ausgebaut
   (Preflight, geführter Flash-Ablauf mit TOTP-Dialog, BOOTSEL-Confirm
   und Log-Ansicht, Audit-Tabelle; `TOTP_SECRET_FILE` nach
   `flash_core.py` konsolidiert) — 11 Tests in
   `tests/test_gui_firmware_tab.py` (168 Tests gesamt).
   **Phase-1-GUI damit komplett** (alle 7 Tabs ausgebaut).
7. ✅ **Erledigt (Nachbereitung).** Intensivprüfung des Gesamtstands
   mit Befund-Tabelle (alle Matrix-Zeilen, Registrierungen, APIs,
   Doku-Zahlen, Smokes ohne Hardware) + Fixes: U+2192-Pfeil in
   `backup list`-Konsolenausgabe (Windows-cp1252-Crash) und 6 weitere
   Stellen auf ASCII vereinheitlicht, cp1252-Regressionstest
   (`tests/test_console_encoding.py`), getpass-Hang ohne TTY per
   isatty-Guard behoben (`--pin-env`-Hinweis), Unit-Tests für
   `pin_core`/`dkek_core` (`tests/test_pin_core.py`,
   `tests/test_dkek_core.py`), Test-Hygiene (keine echten nativen
   PC/SC-Calls in der Suite — Switch-Tests laufen über `keys`).
   192 Tests gesamt, alle ohne Hardware lauffähig. Nach erstem
   Board-Kontakt: Windows-PKCS#11-Fallback-Kette
   (`System32 → OpenSC-Installdir`, Standard-MSI-Layout) in
   `default_pkcs11_lib_path()` + 3 Tests (209 Tests gesamt).
   Nach BOOTSEL-Befund am Ersatzboard (Secure Boot AUS, Debug AN):
   Fingerprint-Gate nur bei Secure Boot an erzwungen
   (`is_secure_boot_enabled()`, sonst Warnung statt Abbruch;
   `PreflightResult` trägt `secure_boot_enabled`/`fingerprint_checked`,
   CLI + GUI zeigen den Skip-Zustand) — 7 Tests in
    `tests/test_flash_core.py` + Skip-Anzeige-Test (199 Tests).
    Nach `otp get`-Wertformat vom Board (`VALUE 0x0000`, Secure Boot
    AUS): Parser auf `otp get OTP_DATA_BOOTKEY0_{0..15}` + VALUE-Zeilen
    umgeschrieben, `read_otp_field` liefert VALUE statt Blob (199 →
    206 Tests). Komponierter Wert am Board verifiziert: 64× `0` wie
    erwartet (OTP-Parser damit Ende-zu-Ende geschlossen).
    Phase 1 read-only am Board: DynOpts-GET + Datetime-GET antworten
    pre-init mit SW=6982 (Re-Test nach `init` nötig); Objektliste ohne
    PIN ok (1 leeres Systemobjekt); PC/SC-Fault-Repro übersprungen
    (Dienst läuft produktiv). Protokolle: `hw-logs/01-survey.txt`,
    `hw-logs/02-phase1-readonly.txt`.
    Nach Login-Messung (6982 ohne Login, 6101/6A86 mit Login) +
    Firmware-Quellenstudium (`cmd_extras.c`): RTC-Datetime ENTFERNT
    (P1=0x0A nicht implementiert, Karte: 6A86); Dynamic-Options-Mapping
    korrigiert (2-Byte-GET, High-Byte-Bits 0x0100/0x0200, 61xx/6Cxx-
    Behandlung); PIN-Login-Voraussetzung dokumentiert (CLI/GUI-Hinweis).
    GET-Fix am Board verifiziert (`OK maske=0x00`, `hw-logs/05-...`).
    Voll-Roundtrip `0x00->0x02->0x03->0x00` am Board verifiziert
    (`hw-logs/06-...`, ohne geforderten Tastendruck — Enforcement
    fraglich, siehe §12). Delete-Befund am Board (`keys delete`
    fand geschriebenes Objekt nicht): leere Suche liefert selbst
    angelegte Objekte nicht zurück (nur PROFILE-Systemobjekt) —
    `list_objects`/`delete_object` auf CLASS-gefilterte Suche
    umgestellt, Tests auf Filter-Mocks umgestellt + 4 neue
    (`hw-logs/07-...`, 207 Tests). Delete-Fix am echten Objekt
    verifiziert (`hwtest01` gelöscht, `hw-logs/08-...`); EC-Paar-Löschung
    ebenfalls (öffentliche Hälfte verschwindet mit, `pkcs15-tool -D`
    leer, `hw-logs/10-...`). Display-Fixes aus Board-Befunden
    (IntEnum-Zahlen, Serial-bytes) + Tests (215 Tests). RSA-Timings
    vermessen (2048: 2:45, 4096: 15:00) — Warntexte auf Messwerte
    umgestellt (`hw-logs/11-...`, `hw-logs/12-...`). PIN-Wechsel/
    Entsperr-Zyklus fehlerfrei (`hw-logs/13-...`). DKEK-Wrap ok,
    Unwrap auf belegter Ref braucht `--force` (Tool-Empfehlung, CLI
    kannte das Flag nicht — nachgerüstet + Tests, `hw-logs/15-...`).
    Roundtrip VOLLSTÄNDIG trotz Fehler-Exit: `--force`-Unwrap importierte
    beide Key-Hälften (Fehler galt dem Cert-Anteil); Detail stand auf
    stdout und wurde unterschlagen → Fehler-Mapping auf stderr+stdout
    erweitert + Tests (`hw-logs/16-...`).
    Zertifikate in List/Delete aufgenommen (Unwrap-Blockade durch
    fid ce01 — unsichtbare Blocker beseitigt; 219 Tests). DKEK-Import-
    `Not allowed` analysiert: kein Code-Fehler, sondern fehlendes
    `--dkek-shares`-Init (Doku-Workflow) — Korrektur in Checkliste +
    `hw-logs/14-...`, kein Commit nötig (nur Doku). Sharing/Belegt-
    Zwei-Prozess-Test: KEIN Konflikt — parallele Sessions + APDU
    funktionieren (Annahme korrigiert, `SessionConflictError` bleibt
    Sicherheitsnetz; `hw-logs/17-...`). Preflight-Fehlschlag analysiert:
    KEIN Parser-Bug — unsigniertes Dev-Build hat keine `signature:`-Zeile
    (nur Partitionstabellen-Hash) → Signatur-Tristate (verified/incorrect/
    absent × SB an/aus) mit Warnung statt Abbruch + `accepted_unsigned`-
    Audit + CLI/GUI-Skip-Anzeige + Tests (`hw-logs/18-...`, 231 Tests).
    Preflight-Fehlschlag analysiert: KEIN Parser-Bug — unsigniertes
    Dev-Build hat keine Rollback-Zeile (Version 3× vorhanden) →
    rollback=-1 (unbekannt) statt Abbruch, Downgrade-Guard (`>= 0`,
    sonst Fehlalarm), Anzeige `6.6 (rollback unbekannt)` via
    `format_version_rollback()` in CLI+GUI + Tests (`hw-logs/19-...`,
    231 Tests). Re-Flash erfolgreich (TOTP + Audit ok, Hash-Chain
    intakt verifiziert); Doppel-Erfolgsmeldung auf UI-seitig
    vereinheitlicht (`hw-logs/20-...`). **Phase-2-Hardware-Validierung
    damit abgeschlossen**. Re-Flash erfolgreich (TOTP + Audit ok); Doppel-
    Erfolgsmeldung (Core-Log + CLI-Echo) auf UI-seitig vereinheitlicht
    (`hw-logs/20-...`). Preflight am Board: PASS mit allen drei Warnungen
    (Fingerprint/SIG/Rollback-Skips greifen live).

**Phase 2 (zurückgestellt):** `gateway_client.py` + `cli/commands/diagnose.py`
+ `diagnose_tab.py`, inkl. Schema-Export/Contract-Test — vollständiges
Design in Anhang A, wird aufgegriffen, sobald Phase 1 abgeschlossen ist
und der Bedarf konkret wird.

## Anhang A: Diagnose-Modus — Design für Phase 2 (zurückgestellt)

Dieser Anhang enthält das vollständig ausgearbeitete Design für den
Diagnose-Modus über das Gateway, das ursprünglich als Teil des
laufenden Umbaus geplant war (siehe §1/§2: aktuell zurückgestellt).
Nichts hiervon ist umgesetzt — der Anhang existiert, damit die
Analysearbeit nicht verloren geht, falls Phase 2 später aufgegriffen
wird.

### `gateway_client.py` — 1:1-Spiegelung von `protocol.rs`

1:1-Spiegelung von `pico-hsm-api-connector/src/protocol.rs`, damit keine
Drift zwischen App und Gateway entsteht:

```python
@dataclass
class GatewayConfig:
    host: str
    port: int
    client_cert: Path
    client_key: Path
    ca_cert: Path

def sign(cfg, key_label: str, mechanism: str, data: bytes) -> bytes: ...
def verify(cfg, key_label: str, mechanism: str, data: bytes, signature: bytes) -> bool: ...
def encrypt(cfg, key_label: str, mechanism: str, data: bytes) -> EncryptResult:  # result, iv, integrity
    ...
def decrypt(cfg, key_label: str, mechanism: str, data: bytes, iv: bytes, integrity: bytes) -> bytes: ...
def derive_and_encrypt(cfg, key_label, derive_mechanism, target_mechanism, peer_public_key: bytes, data: bytes) -> EncryptResult: ...
def derive_and_decrypt(cfg, key_label, derive_mechanism, target_mechanism, peer_public_key: bytes, iv, integrity, data) -> bytes: ...
```

Feldnamen (`key_label`, `mechanism`, `data_b64`, `iv_b64`,
`integrity_b64`, `derive_mechanism`, `target_mechanism`,
`peer_public_key_b64`) exakt wie in `protocol.rs` — Base64-Kodierung
passiert innerhalb der Funktionen, Aufrufer arbeitet mit `bytes`.
Transport: ein `ssl.SSLContext` (`load_cert_chain` + `load_verify_locations`),
ein `socket.create_connection` + `wrap_socket`, ein JSON-Objekt pro Zeile
(`\n`-terminiert), Antwort nach `status`-Feld (`ok`/`denied`/`error`)
auswerten — kein HTTP, kein zusätzliches Package nötig (Python-Stdlib
`ssl`+`socket` reichen).

Aktuelle Mechanismus-Freigabeliste im Gateway (`server.rs::parse_mechanism`
+ `build_aes_cbc_pad`/`build_ecdh1_derive`): `sha256_rsa_pkcs`,
`ecdsa_sha256`, `aes_cbc_pad`, `ecdh1_derive`. Client-seitig vorab
gegenprüfen (klare Fehlermeldung statt rohem Gateway-`error`), Gateway
bleibt aber die alleinige Autorität.

### Sicherheitsauflage für den Betrieb

Das Client-Zertifikat des Diagnose-Modus gehört in `clients.yaml` mit
einem **eigenen, engen Key-Label-Scope** (dedizierte Test-Keys) —
niemals produktive Signing-/Encryption-Keys freigeben. Sonst wird die
App zu einem unauditierten Nebenzugang ins Gateway. Der Diagnose-Tab
sollte das verwendete Zertifikat/CN sichtbar anzeigen.

Neue Config-Datei `~/.pico_hsm/gateway_client.toml` (analog zum
bestehenden `~/.pico_hsm/`-Verzeichnis für Audit-Logs/TOTP-Secret):

```toml
[gateway]
host = "hsm-gateway.internal.test"
port = 8443
client_cert = "/pfad/zu/diagnose-client.pem"
client_key  = "/pfad/zu/diagnose-client-key.pem"
ca_cert     = "/pfad/zu/ca.pem"
```

PIN-Handling bliebe wie bisher (interaktiv/`--pin-env`) — die
Gateway-Zertifikate sind **kein** Geheimnis auf demselben
Vertraulichkeitsniveau wie die HSM-PIN (Client-Auth-Zertifikat, kein
privater HSM-Schlüssel), daher würde ein Pfad in der Config statt eines
Env-Var-Zwangs reichen. Der private Schlüssel des Zertifikats selbst
sollte dennoch mit restriktiven Dateirechten (`chmod 600`) abgelegt
werden.

### Contract-Test gegen `protocol.rs` (Drift-Schutz)

`gateway_client.py` wäre eine von Hand gepflegte Python-Spiegelung
eines Rust-`enum` — das einzige echte Risiko am Python-Ansatz für
diesen Teil der App (siehe §4: deshalb kein Sprachwechsel für die
gesamte App, sondern gezielt hierfür ein Contract-Test):

1. In `pico-hsm-api-connector`: `Request`/`Response` zusätzlich
   `#[derive(schemars::JsonSchema)]` geben (nur als Dev-Dependency,
   kein Einfluss auf den Produktivbinary) und ein Test, der daraus
   `docs/protocol-schema.json` (JSON Schema Draft 7) exportiert.
2. In `pico-hsm-app`: ein Test (`tests/test_gateway_client_contract.py`),
   der die von `gateway_client.py` gebauten Request-Dicts (für alle
   sechs Operationen) mit der `jsonschema`-Bibliothek gegen eine lokale
   Kopie dieser Schema-Datei validiert.
3. Synchronisation der Schema-Datei zwischen den Repos: manuell per
   kleinem Copy-Skript (`tools/sync_protocol_schema.sh`), kein
   CI-Automatismus — passend zur Projektgröße, kein Overkill für ein
   Homelab-Projekt. Schema-Datei wird bei jedem Gateway-Release-Schritt
   neu exportiert und kopiert.

**Grenze dieses Ansatzes:** Er fängt Struktur-Drift ab (umbenannte/
entfernte/neue Pflichtfelder, Typwechsel), **nicht** Semantik-Drift wie
Änderungen an der Mechanismus-Freigabeliste (`parse_mechanism` in
`server.rs`) — die müsste weiterhin manuell mit `gateway_client.py`
synchron gehalten werden.

### Offener Punkt für Phase 2

Der Diagnose-Modus setzt eine laufende, erreichbare
`pico-hsm-api-connector`-Instanz mit passend konfiguriertem
Client-Zertifikat in `clients.yaml` voraus — nicht Teil dieses
App-Repos, muss vor dem ersten Test dort eingerichtet sein.
