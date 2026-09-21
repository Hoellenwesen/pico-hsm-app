# Hardware-Validierung am echten Board (Checkliste)

Gilt für: Ersatzboard (frische Firmware, sonst unberührt). **Niemals auf
einem Produktiv-Token ausführen** — `init`, DKEK-Reset und Flash sind
destruktiv. Alle Ausgaben pro Schritt in `hw-logs/` sichern
(Dateiname `NN-kurzbeschreibung.txt`), am Ende an die
App-Entwicklung zurückmelden (siehe Phase 3).

Referenziert aus: `pico_hsm_tools/flash_core.py`-Moduldoc, README
(„Empfehlung vor Produktiveinsatz"), Architekturkonzept §12 (jeder
Punkt nennt, was genau zu verifizieren ist).

## Phase 0: Windows-Umgebung (einmalig) — ERLEDIGT

- [x] OpenSC-`.msi` installiert. Hinweis: DLL liegt NICHT in System32,
  sondern unter `C:\Program Files\OpenSC Project\OpenSC\pkcs11\
  opensc-pkcs11.dll` — App findet sie per Fallback-Kette automatisch
  (`default_pkcs11_lib_path()`), manuelles `--pkcs11-lib` nur als
  Override nötig.
- [x] `picotool` v2.3.1 mit USB-Support vorhanden
  (`D:\Tools\RaspberryPi-Pico\v2.3.1\picotool`, nicht im PATH —
  bei Bedarf Session-PATH erweitern oder persistent eintragen).
- [x] `age`-Windows-Binary vorhanden (`D:\Tools\age`, nicht im PATH).
- [x] `ssss`: ENTFÄLLT (kein Shamir mehr — Empfänger-Modus, siehe unten).
- [x] Smartcard-Dienst läuft (Automatisch).
- [x] Board per USB: `Pico Key WebCCID Interface` + `Microsoft Usbccid
  Smartcard Reader` da; PC/SC-Reader `Pol Henarejos HID Interface 0`,
  ATR mit „HSM"-Marker.
- [x] Transport-PIN-Status geklärt: KEINE gesetzt (frisches Token,
  `USER_PIN_INITIALIZED=False`) — kein Rate-Versuch nötig gewesen.

## Phase 1: Bestandsaufnahme + Read-only — ERLEDIGT (`hw-logs/01`, `02`)

- [x] `opensc-tool --list-readers`: `Pol Henarejos HID Interface 0`, Karte da.
- [x] `pkcs11-tool --list-slots`: Slot 0, PKCS#15, Applet v6.6, Flags nur
  `rng, token initialized`, PIN min/max 4/8.
- [x] `pkcs15-tool -D`: leere Karte (Version 0).
- [x] `sc-hsm-tool`: v6.6, never initialized, Shares 0, CKV Nullen.
- [x] Firmware: `pico_hsm` v6.6 (RP2350 A2, SDK 2.3.1, Build Sep 2026),
  Secure Boot AUS, Debug AN — aus `picotool info -a` (BOOTSEL).
- [x] OTP-Dump (`picotool otp list`, nur gelesen): Feldnamen
  (`BOOTKEY0_N`, `CRIT1`, `BOOT_FLAGS0`, ...) verifiziert; Parser
  daraufhin auf `otp get` + VALUE umgeschrieben, komponierter
  Fingerprint am Board verifiziert (64× `0`).
- [x] PIN-Status ohne Login (`pin status`): alle Flags False.
- [x] DynOpts-GET/Datetime-GET pre-init: SW=6982; Objektliste ohne PIN
  ok (1 leeres Systemobjekt). Post-init: beide weiter 6982 ohne Login,
  mit Login DynOpts-GET ok (`maske=0x00`), Datetime-GET 6A86
  (Kommando existiert in v6.6 nicht → Code entfernt).

## Phase 2: Schreibende Tests (nur Ersatzboard, Reihenfolge einhalten)

PINs währenddessen in separater, **nicht versionierter** Notiz führen
(nie in `hw-logs/`). Shell-Vorbereitung (einmal pro Fenster):

```powershell
cd D:\Softwareentwicklung\Sonstiges\PicoHSM\pico-hsm-app
.\.venv\Scripts\activate
# DLL-Pfad nur als Override nötig — die App findet die Standard-
# MSI-Installation automatisch (Fallback-Kette). Falls nötig:
$dll = "C:\Program Files\OpenSC Project\OpenSC\pkcs11\opensc-pkcs11.dll"
# Beispiel: pico-hsm-cli --pkcs11-lib $dll status device
```

Wichtig (PowerShell-Falle): **keine Befehls-Strings in Variablen**
packen (`$cli = "pico-hsm-cli ..."` + `& $cli ...` scheitert) —
Befehle immer direkt ausschreiben, Pfade ggf. via `$dll`-Variable
als *Argument* übergeben. Unten bedeutet `` `keys ...` `` immer
`pico-hsm-cli keys ...` (ditto `setup`, `pin`, `dkek`, `backup`,
`firmware`).

0. [x] `init token` — ERLEDIGT (User-Durchlauf). Stand danach per
   `pico-hsm-cli pin status` bestätigen (`user_pin_initialized: True`).
   User-PIN + SO-PIN sind gesetzt (privat notiert).

1. [x] Dynamic Options — Login-Voraussetzung BESTÄTIGT + Voll-
   Roundtrip VERIFIZIERT (`hw-logs/06-...`): `0x00->0x02->0x03->0x00`
   mit exakten Masken; Login persistiert kartenweit (kein PIN-Parameter
   nötig). Auffällig: KEIN Tastendruck trotz aktivem P2C verlangt
   (kein ENABLE_EMULATION im Build) — Enforcement-Umfang offen,
   ggf. Timing-Test nachholen.
   a. [x] `set --key-usage-counter` → `get` = Maske `0x02` ✓
   b. [x] `set --press-to-confirm` → `get` = Maske `0x03` ✓ (KEIN
      Tastendruck verlangt worden — Enforcement fraglich, siehe §12)
   c. [x] `set --no-press-to-confirm` + `set --no-key-usage-counter`
      → `get` = Maske `0x00` ✓ (Ausgangszustand wiederhergestellt)

2. [x] Datenobjekt-Roundtrip: Schreiben + Lesen byte-identisch
   (`fc.exe`: keine Unterschiede). **Delete-Befund behoben +
   verifiziert:** `keys delete` fand das Objekt nicht (leere Suche
   liefert selbst angelegte Objekte nicht — nur PROFILE-Artefakt);
   Fix in `objects_core.py` (CLASS-gefilterte Suche, `hw-logs/07-...`),
   `hwtest01` danach erfolgreich gelöscht (`hw-logs/08-...`).

3. [x] EC-Key-Roundtrip VOLLSTÄNDIG (inkl. Display-Fixes und
   Paar-Löschung: zweites Delete findet nichts mehr, `pkcs15-tool -D`
   bestätigt leeres Token — `hw-logs/09-...` + `hw-logs/10-...`).

4. [x] RSA-2048 mit Stoppuhr: **ca. 2:45 Min. gemessen** (Doku
   sagte >20s — Faktor ~8 daneben!). Warntexte korrigiert (Code,
   CLI-Docstring, Konzept). Delete ok. (`hw-logs/11-...`)

5. [x] **RSA-4096 Hintergrund-Langläufer: ca. 15:00 Min. gemessen**
   (Doku >20min war konservativ — umgekehrter Fall zu 2048).
   Warntexte auf Messwerte umgestellt. `keys delete hw-rsa4096` ok.
   (`hw-logs/12-...`)

6. [x] PIN-Wechsel/Entsperr-Zyklus: alles ohne Fehlermeldung,
   neue PINs jeweils nutzbar (`hw-logs/13-...`).

7. [x] DKEK einfach — DURCHGEFÜHRT 2026-09-21 (`hw-logs/21-...`):
   `init token --dkek-shares 1` (Wipe) → `create-share` + `import-share`
   (interaktiv, Pipe geht nicht) → Shares 1 → EC-Key `hw-dkek1`
   (Ref 1) → `wrap-key` (879B) → `unwrap-key` Ref 2 (Exit-Quirk trotz
   Erfolg, wie `hw-logs/16`) → `keys list` ok. CKV-Anzeige bleibt
   Nullen (kosmetisch, funktional irrelevant). Alter Ablauf unten
   zur Doku erhalten — WICHTIG: Import braucht ein MIT
   `--dkek-shares N` initialisiertes Gerät (Doku-Workflow, sonst
   `Not allowed`, siehe `hw-logs/14-...`):
   `init token --dkek-shares 1` (Wipe + SO-PIN/User-PIN neu, privat
   notieren!) → `dkek import-share hw-share-01.dkek` (Share-Passwort)
   → `dkek status` (Shares 1, CKV ungleich Null erwartet!) → EC-Key
   `hw-dkek1` erzeugen → Ref aus `pkcs15-tool -D` notieren →
   `dkek wrap-key <out> --key-reference <ref>` → `dkek unwrap-key
   <out> --key-reference <ref2>` → `keys list` prüfen.
   HINWEIS (Board-Befund `hw-logs/15-...`): Unwrap auf BELEGTE Ref
   schlägt fehl ("remove key first ... or use --force") — entweder
   FREIE Ref wählen oder `--force` (überschreibt!). Zertifikate sind
   seitdem in `keys list`/`delete` sichtbar/löschbar (blockierten
   zuvor unsichtbar, z.B. fid ce01).

8. [x] Sharing-Mode + Belegt-Exception: KEIN Konflikt — zweite
   exklusive Session + APDU bei gehaltener Session funktionieren
   störungsfrei (`hw-logs/17-...`). PKCS#11 kennt keinen OS-exklusiven
   Session-Lock; `SessionConflictError` bleibt Sicherheitsnetz.

9. [x] Preflight auf `D:\Softwareentwicklung\Sonstiges\PicoHSM\pico-hsm\
   build\pico_hsm.uf2`: PASS wie erwartet (unsigniert + SB aus +
   Rollback-unbekannt, alle Warnungen gezeigt).

10. [x] TOTP eingerichtet (Secret-Datei + Handy-App, Code akzeptiert).

11. [x] **Re-Flash derselben Firmware: ERFOLGREICH** (TOTP ok,
    BOOTSEL per Hand, Fortschritts-Log). Auffälligkeit dabei:
    Erfolgsmeldung doppelt (Core-Log + CLI-Echo) → gefixt
    (Erfolgsmeldung nur UI-seitig; `hw-logs/20-...`).
    `firmware audit verify`: Hash-Chain intakt ✅.

12. [ ] Backup-Roundtrip — per Empfänger-Modus nachholen
    (`backup hsm-backup --recipient ...` + `hsm-restore --identity-file`;
    Export/Restore-Logik ohne Seal-Schicht bereits bewiesen,
    `hw-logs/22-...`).

## Phase 3: Rückmeldung (Format für die Auswertung)

Pro Schritt: Befehl, Exit-Code, relevante Ausgabe (PINs/Secrets
schwärzen!), Dauer bei Timing-Tests (RSA-2048/4096 in Sekunden),
`hw-logs/`-Dateiname. Dazu: alles, was vom erwarteten Verhalten
(§12, README, CLI-Hilfetexte) abwich — inkl. exakter Fehlermeldungen,
Exception-Klassen und APDU-Bytes wo sichtbar.
