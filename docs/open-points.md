# Offene Tests & Punkte — implementierte Funktionen (Stand: 02.10.2026)

Sortiert nach Bereich. Zertifikate stehen separat in `docs/cert-followups.md`
(Batch B / Umfang A implementiert, Hardware-Nachweis offen).

Grundsatz dabei: Alles, was bisher nur synthetisch (Unit) oder gar nicht
verifiziert ist, braucht einmal den Hardware-Gegenbeweis am echten Board.

Voll-Audit 02.10. (3 Agenten + Quellen-Verifikation gegen pico-hsm-Sources):
SO-PIN-Hex-Inkonsistenz, Unwrap-6A80-Totpfad, Header-Fehler (0x1B/0xFB),
Session-Clear-Lücke, Restore-Cap, Busy-Guards, Serial-0x82, CSR-Attributes,
stale Copy, leere CE — alle behoben. False Positives entlarvt (Tauri
camelCase-Autokonvertierung). Rest-HW-Nachweise unten.

## A. Schlüssel (Keys-Tab) — Hardware-Gegenbeweis offen

1. **Generierung je Typ/Größe/Kurve**: AES-128/192/256, RSA 1024–4096,
   alle EC-Kurven inkl. Labels, Usage-Counter, Purpose-Restrictions (0x91).
   GAK-Bytes sind bisher nur synthetisch getestet (`gak_*`-Tests).
2. **Slow-Keygen-UX**: RSA-4096-Bestätigungscheckbox, Minuten-Blockade,
   Fortschritt/Timeouts/ESC-Verhalten während der Mutex-Blockade.
3. **Delete einzeln + Bulk**: Fortschritts-/Ergebnis-Modal, Missing-Skips,
   „device key nie selektierbar“, DKEK-Warntext prüfen.
4. **Rename-Roundtrip**: Layout A/B/C aus realen Dumps belegt (Unit grün),
   aber Schreiben + Zurücklesen + Label-Inherit (CE erbt C4) am Gerät prüfen.
5. **Generation-Records** (`localStorage`, Board-Serial + Key-ID): mehrere
   Boards, ID-Wiederverwendung nach Delete, Stale-Einträge.
6. **AES-Details**: `type_source`-Heuristik und Usage-Wort (0x0803) am Gerät
   gegen `pkcs15-tool` gegenprüfen.
7. **`set_label` auf C8/C9** (CA-Description): Pfad existiert, nie verwendet/
   getestet — entscheiden: testen oder Guard einengen.

## B. PIN / Session / Init

1. **User-PIN ändern, SO-PIN ändern, Unblock mit SO-PIN** inkl. Retry-Zähler,
   Blocked-Zustand und Fehlermeldungen am Gerät durchspielen.
2. **Session-PIN**: RAM-only, Logout/Lock, Clear bei Disconnect/Reconnect.
3. **PIN-Regeln angleichen**: `verify_pin` erlaubt 1–32, `unblock` 1–16,
   Copy spricht von 6–16 — Frontend-Validierung und Backend-Guards auf eine
   Linie bringen und testen. **SO-PIN übergreifend (Audit 02.10.)**: Init
   sendete Roh-ASCII, Change/Unblock hex-dekodieren (Ecosystem-Standard) —
   behoben (Init nutzt `decode_sopin_hex`, UI „16 hex chars“); HW-Nachweis:
   Init + Change mit gleichem SO-PIN-Wert.
4. **Init-State per Probe** (Spike ersetzt 02.10.): `NotInitialized` (6A88)
   aus der PIN-Probe → `live.init`; Verhalten an frischem Gerät noch per
   Hardware belegen. Init-UI siehe Slice 2 (Roadmap).
5. **Options-Bits**: `resetRetryCounter` (0x0001), `secureLock` (0x0400),
   `resetOnly` (0x0020) werden gelesen, aber nirgends geschrieben/verwendet —
   entscheiden: Feature oder Anzeige streichen.

## C. Device Config / Diagnose

1. **Dynops Roundtrip**: Press-to-confirm + Key-Counter setzen, zurücklesen,
   Anzeige nach Reconnect (Slow-Tier) prüfen.
2. ~~**`set_phy` ohne UI**~~ — erledigt 01.10.: `get_phy`/`set_phy` + Typen
   komplett gestrichen (kein UI-Aufruf; PHY käme als eigener Slice neu).
3. **Diagnose-Konsole**: Raw-APDU + ATR-Ausgaben, Fehler-Drosselung bei
   Dauerfehlern (keine Fehler-Flut im Polling).
4. **Rescue-Werte**: Memory/Flash/Secure/Board-ID über `80 1E …` gegen
   Rescue-`list`-Erwartung prüfen; `firmware_bytes=null` auf Nicht-Pico.

## D. Polling / Verbindung / Reboot (neu, unbeobachtet)

1. **Tiered Polling am Gerät**: Uhr läuft flüssig, Klicks blockieren nicht
   mehr spürbar; Fast=Status/RTC/Retries, Slow=Rest pro Connection.
2. **Reconnect**: Abstecken → Offline + Session-Clear; Anstecken → Slow-Tier
   genau einmal, keine Fehler-Flut.
3. **BOOTSEL-Reboot + Rückkehr-Toast** („Device back online“) am Pico prüfen.
4. **RTC-Auto-Sync** nach Power-Loss (Rescue-GET 6985 → SET → stiller Sync).
5. **Multi-Reader**: es wird nur `readers[0]` benutzt — Zweit-Reader wird
   ignoriert; bewusst so lassen und dokumentieren oder Auswahl-UI planen.
6. **CSP-Laufzeitcheck**: einmal DevTools-Konsole auf CSP-Verstöße prüfen
   (theoretisch keine).

## E. Platzhalter (erledigt)

1. ~~Mock-Buttons, Dummy-Tabs~~ — erledigt: Backup-Tab ist seit Slice 3 echt
   (DKEK + Wrap/Unwrap), Logs-Tab seit Slice 4 (App-Audit-Journal),
   `DummyPage.tsx` gelöscht. Keine Platzhalter-Tabs mehr.
2. Diese Tabs sind eigene Slices (bereits so geplant), kein Teil der
   P0-Funktionen.

## F. Cleanup / Tests / Supply-Chain

1. ~~**`greet`-Template-Command**~~ — erledigt 01.10.: gestrichen.
   Sidebar-Pillen (Firmware-„P0“, Logs-„TBD“) ebenfalls entfernt.
2. **Frontend-Tests**: `backup.test.ts` (Bundle-Serde, Share-Normalisierung)
   existiert seit Slice 3; offen nur noch `genRecords.ts` o.ä.
3. **`cargo audit`**: 0 Vulnerabilities, 3 informative Warnungen
   (proc-macro-error unmaintained, glib unsound/Linux-only, yoke-derive
   yanked) — bei Gelegenheit `cargo update -p` prüfen. Reports werden nicht
   im Repo abgelegt (auf Bedarf laufen lassen); letzter Stand 01.10.2026.
4. **`npm audit`**: 0 Vulnerabilities (Stand 01.10.2026) — nach
   Dependency-Änderungen wiederholen.

## G. Firmware-Flash (Erfahrung 01.10.2026: Board blieb in BOOTSEL)

1. **Family-Tabelle korrigiert** (SDK v2.3.1 `boot/uf2.h` als Quelle):
   `56`=RP2040, `57`=ABSOLUTE (Meta, keine MCU), `58`=DATA, `59`=RP2350-ARM-S,
   `5A`=RP2350-RISC-V, `5B`=RP2350-ARM-NS. Frühere Annahme (`57`=ARM) war
   falsch; das `pico_hsm.uf2` (`rp2350-arm-s`-Build) meldet korrekt ARM.
2. **Stuck-Ursache eingekreist**: Bytes waren korrekt (selbe Datei lief
   manuell), Secure Boot ist AUS, Board recovers per Replug. Verdacht jetzt:
   Transfer (kein `sync_all`, LFN-Name, fehlender Size-Check) — Copy-Pfad
   gehärtet: 8.3-Name `picohsm.uf2`, `sync_all` (FlushFileBuffers),
   Größen-Check nach Close, 3 s Settle. `StuckBootsel`-Hint nennt das
   Explorer-Gegenexperiment.
3. **Response-Race im Reboot (Log-Beweis 01.10.)**: `rescue.c` verarbeitet
   `EV_RESET` asynchron — der Reset kann die 9000-Antwort überholen, Host
   sieht `TransmitFailed`, obwohl der Reboot läuft. `reboot_device` ist jetzt
   tolerant: Transportfehler beim Reboot-APDU → Outcome-Poll (~20 s, Drive
   bzw. Version) → erst dann Erfolg/Fehler. Gilt für Flash-Flow und
   Sidebar-Reboot. UI sagt jetzt überall „Knopf drücken, wenn gefragt“.
4. **Drive-Scan fand nie etwas (Root Cause 01.10.)**: `.ok()?` in der
   A–Z-Schleife brach beim ersten fehlenden Laufwerk (praktisch immer `A:`)
   ab — kein Copy, kein Reboot, ewiges `waiting-drive`/Timeout. Fix:
   `continue` + UF2-Marker-Check. Alles davor (Transfer-Härtung,
   power-cycle-Phase) war auf der falschen Fährte gebaut, schadet aber nicht.
4. **Sticky-BOOTSEL-Hypothese (01.10.)**: Datei weg + Drive da = Bootloader
   hat konsumiert, kehrt aber per Software-Eintritt im Kreis zurück
   (Watchdog-Scratch überlebt Watchdog-Resets, kein Power-Cycle). App zeigt
   `power-cycle`-Phase mit Abziehen/Anstecken-Anweisung und beendet den Flow
   danach automatisch (Version lesen). Rückfall-Timeout 300 s.
3. **Entscheidendes Experiment (User)**: selbe Datei per Explorer in RPI-RP2
   kopieren (Button-BOOTSEL) — rebootet das Board, liegt es am App-Transfer;
   bleibt es auch dort kleben, liegt es an Datei/Bootloader (Meta-Block?
   dann SDK-Blink-UF2 als Isolationstest).
3. **Stuck-Recovery**: Bleibt RPI-RP2 nach Copy bestehen → Board abziehen/
   RESET → alte Firmware bootet (Payload wurde ignoriert). Per Mass-Storage
   ist kein Reboot erzwingbar.
4. **Echte UF2-Form belegen**: elf2uf2-Images haben ABSOLUTE-Meta-Block +
   Payload-Sequenz; Parser toleriert das, verlangt aber ≥1 komplette
   Sequenz. Neue Image-Varianten ggf. gegenprüfen.
5. **Hardware-Tests offen**: Full-Flash per App am 01.10. erfolgreich
   nach Drive-Scan-Fix (vorher Root Cause Nr. 4).
6. **Offene HW-Pfade**: Hash-Mismatch-Abbruch vor Reboot (Board bleibt
   online), Sidecar-Autoerkennung Ende-zu-Ende, PresenceRequired ohne
   Knopfdruck, BootselTimeout (60 s ohne Drive), FamilyMismatch-Refuse vor
   Reboot, power-cycle-Phase (real noch nie durchlaufen), RebootTimeout
   (Drive weg, Board kommt nicht zurück), Normal-Reboot über Toleranzpfad,
   Polling-Rauschen während BOOTSEL (harmlos per Design, nie beobachtet).
7. **Unit-Lücken**: `read_sidecar_hash` mit Temp-Dateien (beide Suffixe,
   Müll, fehlend), 3-MB-Cap in `parse_uf2_file`, ungenutztes
   `find_bootsel_drive`-Command (in Diagnose nutzen oder streichen).
8. **Beobachtbarkeit**: Toleranzpfad (Race-Recovery) sollte ins Flash-Log
   stempeln; bestätigter Erfolgsweg des 01.10.-Flashs unbekannt
   (clean vs. Recovery).
