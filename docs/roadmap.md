# Roadmap (Stand: 01.10.2026)

Grundsätze: HSM-only, keine Mocks, keine Demo-Werte. Jede Slice besteht aus
Implementierung + Unit-Tests + Hardware-Nachweis. Reine HW-Gegenbeweise
stehen pro Slice dabei; die ausführlichen HW-Checklisten bleiben in
`docs/open-points.md` und `docs/cert-followups.md` und werden verlinkt.

## Erledigt (Kurzprotokoll)

- App-Scaffold (Tauri 2 + React 19 + TS), Dashboard mit Live-PCSC-Werten.
- Keys: AES/RSA/EC-Generierung, Labels, Usage-Counter, Purpose-Restrictions,
  Details, Einzel- + Bulk-Delete, Generation-Records.
- Zertifikate Batch A (Liste, CVC-Parsing, PEM-Export, CA-Delete) + Batch B
  (Umfang A: X.509-Import, Validierung, Key-Match, Readback-Verify,
  Roh-Download).
- PIN-Management (ändern, unblocken, Session-PIN, Retries), Device Config
  (Dynops, Diagnose mit Raw-APDU-Konsole), tiered Polling, CSP,
  Supply-Chain-Audits (npm + cargo, 0 Vulnerabilities).
- Firmware-Flash (UF2-Validierung, BOOTSEL-Flow, SHA-256 optional,
  toleranter Reboot, Flash-Log, power-cycle-Phase).
- Cleanup (greet, PHY-Code, Dummy-Mocks, Sidebar-Badges entfernt).

## Slice 1: CSR-Export (Batch B2)

Schließt den externen Signing-Loop: Generieren → CSR → CA-signieren →
Import (Import existiert seit Batch B).

- PKCS#10-Export für Device-Key-IDs (DER + PEM), Session-PIN-Flow wie Export.
- Offene Entscheidungen aus Batch B: Attribute ja/nein, Datei-Naming,
  UI-Ort (Keys-Zeile vs. Certificates-Tab).
- Kein Chain-Trust, keine CA-Validierung (Management-Tool, kein PKI-Validator).
- HW-Nachweis: CSR mit `openssl req -noout -text` prüfen; Roundtrip mit
  CA-signiertem Zertifikat zurück importieren (byte-identisch).
- Verlinkt: `docs/cert-followups.md` (B2-Abschnitt, CA-Import ausdrücklich
  weiter offen gelassen).

## Slice 2: Initialisierung (implementiert 02.10., HW-Nachweis offen)

- `init_device` (`00 50 00 00` + TLV 0x81/0x82/0x91/0x92): User-PIN 6–16,
  SO-PIN 8–16 Roh-ASCII, Retries 1–15 (Default 3), DKEK None/N-Slots (kein
  „random“ in der UI: geräteseitig erzeugter DKEK ist nie sichtbar → keine
  Shares → kein Restore; nur N-Slots mit externen Shares dienen Backup).
- Init-State per `NotInitialized`-Probe (Spike ersetzt); Dashboard-Banner +
  DeviceConfig-Sektion; Session-Übernahme nach Init; Checkbox+Danger-Confirm.
- Stale „later slice“-Hints repariert.
- HW-Nachweis offen: Scratch-Board (Wipe!), SO-PIN-Mindestlänge + DKEK-Tag-
  Semantik belegen, Retry-Zähler, Re-Init-Löschung; siehe `open-points.md` (B).

- Device-Init-Flow (User-PIN/SO-PIN-Setup auf frischem Gerät).
- Init-Heuristik belegen und `useDevice`-Spike auflösen („initialized“ vs.
  „unknown“ anhand frischen/uninitialisierten Geräts).
- PIN-Regeln angleichen als Teil davon: `verify_pin` (1–32), `unblock`
  (1–16), Copy (6–16) + Frontend-Validierung auf eine Linie bringen.
- HW-Nachweis: frisches Gerät von Null auf initialisiert, Retry-Zähler,
  Blocked-/Unblock-Wege; siehe `docs/open-points.md` (B).

## Slice 3: Backup & Restore (DKEK) (implementiert, HW-Nachweis offen)

- DKEK-Status (`00 52`, total/restlich/KCV/XKEK-Flag), Share-Import (XOR
  N-of-N, KCV-Proof, Session-PIN für Finalize), Share-Generierung (WebCrypto,
  einmalige Anzeige, nie gespeichert). Domain-Setup (`00 52 01`, Login
  nötig): Domain 0 kommt aus Init, 1–15 frei definierbar.
- Wrap (`00 72`, Purpose-Gate, AES braucht Button) + Unwrap (`00 74`,
  Extended-APDU, Domain-Suche geräteseitig, WrongDkek ehrlich) + versioniertes
  JSON-Bundle (validiert, IDs belegt → Skip statt Overwrite) + Restore mit
  Ergebnisliste.
- Vitest angelegt (`npm test`): Bundle-Serde + Share-Normalisierung.
- Domain-Bindung (Firmware-Wahrheit): Frisch generierte Keys landen IMMER in
  Domain 0 (Vererbung nur bei ID-Wiederverwendung); eine Domain-Auswahl bei
  Generierung existiert firmware-seitig nicht — daher keine in der UI. Echte
  Domain-Zugehörigkeit kommt per Unwrap (Meta-Tag 0x92 = entschlüsselnde
  Domain). Wrap nutzt automatisch die Domain des Keys; Restore matcht DKEKs
  inhaltsbasiert (KCV), Bundle-`domain` ist rein informativ.
- HW-Nachweis offen: 2-Board-Roundtrip (Shares → KCV gleich → Wrap →
  Bundle → Unwrap → Signaturprobe), Negativ-Fälle (falscher Share,
  Purpose-Verbot, belegte ID, falscher DKEK).

- DKEK-Shares erzeugen/verwalten, Key-Wrap/Unwrap, Restore-Flow — jeweils
  mit Confirm-Dialogen (destruktive Schritte) und ehrlichen Fehlerpfaden.
- Ersetzt den Backup-Dummy-Tab.
- HW-Nachweis: Backup erstellen → Keys löschen → Restore → Keys wieder
  nutzbar (Signatur-/Entschlüsselungsprobe).

## Slice 4: Logs / Audit (implementiert 02.10.)

Firmware gibt keine Geräte-Logs her (verifiziert: Rescue nur
KEYDEV_SIGN/WRITE/SECURE/READ/REBOOT, HSM-Applet ohne Log-/Counter-Read,
`debug.c` nur Compilezeit) — daher App-Journal, ehrlich als „App-Aktivität“
gelabelt.
- `auditLog.ts`: Mutationen nur (keine Reads), nie Secrets (keine PINs,
  Shares, Blobs, Subjects, Pfade), Ring-Buffer Cap 500, localStorage +
  JSON-Export + Clear mit Confirm.
- Verdrahtet in allen `useDevice`-Mutationen + PIN-Forms (Erfolg + Code bei
  Fehler); Logs-Tab mit Tabelle, Filter, Suche, Export, Clear; DummyPage
  entfernt.
- Vitest: Ring/Clear/korruptes Storage/Shape (Secret-Freiheit).

- Umfang vorab klären: Was gibt die Firmware an Logs her?
  (Rescue-Applet? Zähler? Fehlerspeicher?) Danach Tab-Dummy ersetzen,
  ggf. mit Export-Funktion.
- HW-Nachweis: angezeigte Einträge gegen Geräteaktionen abgleichen.

## Slice 5: Firmware-Follow-ups (klein, bündeln)

- Toleranzpfad (Race-Recovery) ins Flash-Log stempeln.
- `find_bootsel_drive`-Command in Diagnose nutzen oder streichen.
- Unit-Tests: `read_sidecar_hash` (Temp-FS, beide Suffixe, Müll, fehlend),
  3-MB-Cap in `parse_uf2_file`.
- Übrige HW-Fehlerpfade: Hash-Mismatch-Abbruch, Sidecar-E2E,
  PresenceRequired, BootselTimeout, FamilyMismatch-Refuse, power-cycle-Phase,
  RebootTimeout.
- Ausdrücklich später/separat: Linux-Laufwerkserkennung,
  GitHub-Release-Check, ESP32-Flasher.

## Slice 6 (optional): PHY / Commissioning

Nur nach Bedarf aktivieren. Codebase ist bereinigt (kein Alt-Code im Weg).

- Read (+ Write mit starkem Confirm + USB-Brick-Warnung, Reboot-Hinweis):
  VID/PID, USB-Produktstring, LED (GPIO/Helligkeit/Treiber), USB-Interfaces,
  Curves-Bitmaske, OPTS, UP-Button.
- Protokoll-Fakten aus Git-Historie rekonstruieren (`80 1E 01` lesen,
  `80 1C 01` schreiben, TLV-Tags 0x00/0x04–0x06/0x08/0x09/0x0A–0x0C).
- HW-Nachweis: Read gegen Rescue-Dump, Write nur mit Recovery-Plan
  (falsche Werte brechen USB-Zugang → BOOTSEL-Reflash).

## Querschnittlich (kein eigener Slice)

- Frontend-Tests (Vitest) für `genRecords.ts`/Datei-Helfer prüfen.
- `npm audit` / `cargo audit` bei jeder Dependency-Änderung wiederholen.
- CSP-Laufzeitcheck in DevTools (einmalig, theoretisch keine Violations).
- Multi-Reader: nur `readers[0]` — dokumentieren oder Auswahl-UI planen.
- Options-Bits (`resetRetryCounter`, `secureLock`, `resetOnly`): Feature oder
  Anzeige streichen.
- `set_label`-Guard für C8/C9: testen oder einengen.
