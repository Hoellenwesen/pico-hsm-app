# Abnahme: Pico HSM App — Betriebsbereitschaft

Stand: 02.10.2026. Konsolidiert aus `roadmap.md`, `docs/open-points.md`,
`docs/cert-followups.md` (diese Dateien sind damit ersetzt und entfernt).

## Rollen & Konventionen

- **Board S (Scratch)**: destruktive Tests frei (Init-Wipe, Flash, Deletes,
  Re-Init). Darf jederzeit neu aufgesetzt werden.
- **Board P (Partner)**: Zustandspartner für 2-Board-Proofs (DKEK-Cross-Check,
  Restore-Ziel). Keine Wipes ohne Absprache.
- Jedes Item: `[ ]` offen / `[x]` bestanden mit Datum + Kurzbefund.
- **Stop-Regel**: Bei Datenverlust-Verdacht (unerwarteter Wipe, falscher Key
  weg) sofort abbrechen, Stand sichern, Befund notieren.
- **Vorab**: DKEK-Backup beider Boards, bevor destruktive Phasen laufen.
- Negativ-Fälle gehören dazu: Jeder Fehlerpfad muss die *erwartete*,
  ehrliche Meldung zeigen (Code + Hinweis), nie Stille oder Crash.

## Phase 0 — Baseline & Umgebung (S + P)

- [ ] Beide Boards verbinden: Dashboard zeigt plausible Werte (Version, MCU,
  Produkt HSM, Board-ID notieren).
- [ ] Nur ein Reader aktiv; `readers[0]`-Verhalten notiert (Multi-Reader:
  Zweit-Reader wird ignoriert — bewusst, keine Auswahl-UI).
- [ ] DevTools-Konsole: keine CSP-Verstöße beim Bedienen aller Tabs.
- [ ] Offline: nur Dashboard klickbar (Tooltip „Connect a device first“);
  Abstecken auf Keys-Tab → Auto-Sprung Dashboard; Anstecken → keine
  Auto-Navigation.
- [ ] Session-Unlock-Button (primary) öffnet PIN-Dialog; Lock-Button sperrt.
- [ ] Suiten grün mit Datum: `cargo test`, `cargo check --all-targets`
  (warnungsfrei), `npm run build`, `npm test`, `npm/cargo audit`.

## Phase 1 — Keys (Board S)

- [ ] Generierung je Typ/Größe/Kurve (AES-128/192/256, RSA 1024–4096, alle
  EC-Kurven) inkl. Label, Usage-Counter, Purpose-Restrictions (0x91).
- [ ] Slow-Keygen-UX (RSA-4096): Checkbox-Pflicht, Minuten-Blockade mit
  Hinweis, ESC/Backdrop-Verhalten, kein Doppel-Submit.
- [ ] Delete einzeln + Bulk: Fortschritt/Ergebnis-Modal, Missing-Skips,
  Device-Key nie selektierbar, DKEK-Warntext gelesen.
- [ ] Rename-Roundtrip (Layouts A/B/C): Schreiben → Zurücklesen identisch,
  CE erbt C4-Label.
- [ ] Generation-Records: Zweit-Board (P) zeigt eigene Records;
  ID-Wiederverwendung nach Delete; Stale-Einträge nach Fremd-Löschung.
- [ ] AES-Spotcheck: `type_source`/Usage-Wort gegen `pkcs15-tool` plausibel.
- [ ] `set_label` auf C8/C9: Entscheiden — testen oder Guard einengen
  (Befund hier notieren).

## Phase 2 — PIN / Session / Init (Board S, destruktiv ok)

- [ ] User-PIN ändern, SO-PIN ändern, Unblock mit SO-PIN — inkl. Retry-Zähler,
  Blocked-Zustand, Fehlermeldungen.
- [ ] Session: RAM-only (Reload vergisst), Logout/Lock-Button, Clear bei
  Disconnect/Reconnect (kein hängender PIN-Dialog).
- [ ] **SO-PIN übergreifend**: Init + Change mit gleichem SO-PIN-Wert
  (Hex-Standard) — muss in beide Richtungen funktionieren.
- [ ] Init-Wipe: Banner „Not initialized“ → Setup (User 6–16, SO 16 Hex,
  Retries Default 3, DKEK None/N-Slots) → Checkbox + Danger-Confirm →
  Session ohne Extra-Login übernommen, Banner weg.
- [ ] Retry-Limit wirkt (falscher PIN → 63CX-Degradation); Re-Init löscht
  Keys (Checkbox-Pfad bewusst durchlaufen).
- [ ] DKEK-Tag-Semantik belegen (None vs. N Slots → EF_DKEK-Sicht in `list`).

## Phase 3 — Zertifikate (Board S)

- [ ] Import-Roundtrip: X.509 importieren → Liste `Stored` + Format + Typ/
  Größe/Kurve → Download → **byte-identisch** zum Input
  (+ `openssl x509 -noout -text`).
- [ ] Key-Mismatch: fremdes Zertifikat → `CertKeyMismatch`, nichts geschrieben.
- [ ] No-op: identische Datei → „already stores … nothing changed“, kein Rewrite.
- [ ] Überschreib-Restore: nach Fehlschlag alter Inhalt wieder da
  (sofern provozierbar, sonst als nicht-provozierbar notieren).
- [ ] PIN-Flow: ohne Session → 6982 → Login → erneut.
- [ ] Modal-Regeln am Import-Confirm (Button/Backdrop/ESC, busy-sperre).
- [ ] UPDATE-auf-fehlende-CE: Befund — geht es (Create-Pfad) oder kommt
  ehrliche Absage? Hier notieren.
- [ ] Test-Keygruppe danach sauber löschen.

## Phase 4 — CSR (Board S)

- [ ] Export mit CE-Zertifikat → `openssl req -noout -text -verify` ok
  (Signatur + Subject + SPKI).
- [ ] Export ohne CE, mit Gen-Record (diese App generiert) → SPKI passt zum Key.
- [ ] Negativ: AES-Key → `RefusedCsr`; ohne CE/Record → `NoPubkey`;
  BadSubject (leere CN, 3-Buchstaben-C); Purpose-Verbot → `SignNotAllowed`.
- [ ] Counter-Verbrauch: Key mit kleinem Limit → Zähler sinkt; erschöpft →
  `CounterExhausted`.
- [ ] **Full Loop**: CSR → extern/CA signieren → Import → byte-identisch
  lesen + Liste zeigt X.509 (schließt Batch B).
- [ ] Sondergrößen: RSA-4096 (Extended-APDU, 512-Byte-Signatur) + EC-P521.

## Phase 5 — DKEK / Backup (Board S + P, Herzstück)

- [ ] Shares erzeugen (N=3 empfohlen) → sofort verteilen/speichern →
  Screen-Discard.
- [ ] Beide Boards importieren (Reihenfolge egal) → **KCV identisch** auf
  S und P. Falscher Share → KCV-Drift (Negativ).
- [ ] Domain-Setup: Domain 1 mit N anlegen → Status 0/N; belegte Domain →
  `DomainExists`.
- [ ] Wrap aller Keys (S) → Bundle (Metadaten + KCV prüfen) → Download.
  Ohne WRAP-Purpose → sauberer Skip; AES → Button-Press-Hinweis.
- [ ] Restore auf P (gleicher DKEK, KCV geprüft) → alle Einträge ok →
  **Signaturprobe pro Key**.
- [ ] Negativ: belegte ID → Skip (kein Overwrite); falscher DKEK →
  `WrongDkek`; PIN-Flow; korrupte Bundle-Datei → Absage vor Zugriff.
- [ ] Restore-Überschreibschutz nie umgangen (Stichprobe Quellcode ok).

## Phase 6 — Firmware (Board S)

- [ ] Hash-Mismatch → Abbruch **vor** Reboot (Board bleibt online).
- [ ] Sidecar daneben → auto-erkannt + verifiziert; Paste-Alternative.
- [ ] PresenceRequired ohne Knopfdruck → Meldung + Retry.
- [ ] BootselTimeout (Drive absichtlich nicht erzeugen) → sauberer Abbruch.
- [ ] FamilyMismatch (falsches Image) → Refuse **vor** Reboot.
- [ ] Re-Flash-Nachweis (kein Zweit-Image vorhanden): Version gleich +
  Flash-Log lückenlos (Start → waiting-drive → flashing → waiting-reboot →
  Board-back + Version). Update alt→neu sobald Zweit-Image existiert.
- [ ] power-cycle-Phase / RebootTimeout / StuckBootsel: nur falls auftretend
  protokollieren (nicht provozieren).

## Phase 7 — Verbindung / Polling / UX / Logs (beide Boards)

- [ ] Tiered-Gefühl: Uhr läuft flüssig, Klicks blockieren nicht spürbar.
- [ ] Reconnect: Abstecken → Offline + Session-Clear + Auto-Dashboard;
  Anstecken → Slow-Tier einmal, keine Fehler-Flut.
- [ ] BOOTSEL-Reboot + „Device back online“-Toast.
- [ ] RTC-Auto-Sync nach Power-Loss (still, ohne Toast-Spam).
- [ ] Logs-Tab: Mutationen erscheinen mit korrekten Details; Stichprobe
  Secret-Freiheit (keine PINs/Shares/Blobs/Subjects/Pfade); Reload persistent;
  Export valides JSON; Clear mit Confirm; Raw-APDU nur als Applet+INS+Länge.
- [ ] Dynops-Roundtrip (setzen → zurücklesen → Reconnect-anzeige).
- [ ] Diagnose-Konsole: Raw-APDU + ATR plausibel, keine Fehler-Flut bei
  Dauerfehlern; Rescue-Werte gegen Erwartung.

## Teil II — Rest-Implementierung (nach Abnahme)

- **Slice 5 Firmware-Follow-ups**: Toleranzpfad ins Flash-Log stempeln;
  `find_bootsel_drive` in Diagnose nutzen oder streichen; Unit-Tests
  `read_sidecar_hash` (Temp-FS) + 3-MB-Cap; oben offene HW-Pfade nachholen.
- **Slice 6 PHY/Commissioning (optional)**: Read (+ Write mit Confirm +
  USB-Brick-Warnung); Protokoll (`80 1E 01`/`80 1C 01`, Tags
  0x00/0x04–0x06/0x08/0x09/0x0A–0x0C); Write nur mit Recovery-Plan.
- **Querschnitt**: `genRecords.ts`-Test; Audit-Rhythmus bei Dep-Änderungen;
  Multi-Reader-Entscheid; Options-Bits-Entscheid; `set_label`-C8/C9-Entscheid
  (siehe Phase 1).
- Ausdrücklich später/separat: Linux-Erkennung, GitHub-Release-Check,
  ESP32-Flasher, CA-Import, Chain-Trust, EdDSA-CSR.

## Teil III — Abnahme-Protokoll

| Phase | Ergebnis | Datum | Bemerkung |
|-------|----------|-------|-----------|
| 0 Baseline | | | |
| 1 Keys | | | |
| 2 PIN/Session/Init | | | |
| 3 Zertifikate | | | |
| 4 CSR | | | |
| 5 DKEK/Backup | | | |
| 6 Firmware | | | |
| 7 Verbindung/UX/Logs | | | |

Gesamtverdict: ____________________ Datum: __________ Version: __________

## Anhang R — Release-Checkliste Windows (pro Release)

- [ ] Version in `src-tauri/tauri.conf.json` + `package.json` + `src-tauri/Cargo.toml` identisch getaktet.
- [ ] Sauberer Build: `npm run tauri build` ohne Fehler/Warnungen.
- [ ] Größen protokolliert (`dist-release/`, `tools/package-windows.ps1`):
  `*-setup.exe` (~1,6 MB), `*-portable.exe` (~4,6 MB), `*-portable.zip`
  (~1,9 MB). Abweichung >50 % → Ursache klären.
- [ ] Portable-Exe: starten, Board verbinden, PIN-Login (kein Installer nötig).
- [ ] Portable ohne Dev-Server: Fenster lädt (kein `:1420`). Regression:
  `custom-protocol`-Feature in `src-tauri/Cargo.toml` ist Pflicht — ohne es
  lädt das Release-Binary die devUrl (Build schlägt nicht fehl, läuft aber
  nicht standalone!). Prüfpunkt: Remote-Debugging-URL ist
  `http://tauri.localhost/`, nie `http://localhost:1420/`.
- [ ] NSIS-Setup auf frischem Windows (VM): Installation ohne Admin-Rechte,
  WebView2-Bootstrapper läuft (Internet nötig), Uninstaller vorhanden.
- [ ] Portable-Zip: entpacken, `pico-hsm-app.exe` starten, Board verbinden,
  PIN-Login, Key anlegen + löschen.
- [ ] SmartScreen-Text gelesen und akzeptiert (kein Signing in 0.x);
  SHA-256 der Artefakte auf der Release-Seite veröffentlicht.
- [ ] `tools/package-windows.ps1` läuft von Null durch (Repo-Root).
- [ ] Release-Notes: Version, Änderungen seit Vorgänger, bekannte
  Einschränkungen (aus Abnahme-Protokoll übernehmen).
