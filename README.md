# pico-hsm-app

Konfigurations-App (CLI + GUI) für den **Pico-HSM** — ein Homelab-HSM
auf Basis Raspberry Pi Pico 2 / RP2350. Die App deckt den kompletten
Token-Lebenszyklus ab: Ersteinrichtung, PIN- und DKEK-Verwaltung,
Schlüssel-/Objekterzeugung, age-Backups (Empfänger-Modus) und verifizierte
Firmware-Updates.

Deckwort für dieses Projekt: **eine App, zwei Oberflächen, eine
Logikquelle** — CLI und GUI bedienen identische Core-Module, es gibt
keine Logik-Duplikation.

## Warum eine eigene App?

- Der Pico-HSM ist ein **PKCS#11-Device**: Es gibt keine „fertige"
  App, die Token-Init, DKEK-Shares, PIN-Verwaltung, HSM-Backup und
  signierte Firmware-Updates in einem konsistenten Workflow bündelt.
- Die kritischen Operationen laufen über **vertrauenswürdige externe
  Binaries** (`age`, `sc-hsm-tool`, `picotool`/`picotool`),
  nicht über Neuerfindung von Kryptografie in Python.
- Das Sicherheitsmodell ist der Kern: Board-Fingerprint → Signatur →
  Anti-Rollback → TOTP → Flash → Hash-Chain-Audit-Log.

## Features

| Bereich | Was | CLI-Gruppe | GUI-Tab | Modus |
|---|---|---|---|---|
| Lebenszyklus | Token-Init, PIN-Ändern/Entsperren, DKEK-Shares | `init`, `pin`, `dkek` | Start/Setup/PIN/DKEK | Normal |
| Schlüssel | RSA/EC/AES generieren, Objekte lesen/schreiben, Zufall | `keys` | Schlüssel | Normal (+Login) |
| Backup | age-Verschlüsselung, Empfänger-Modus (1-aus-n) + HSM-Backup | `backup` | Backup | immer |
| Sicherheit | OTP-Anzeige, Dynamic Options, Fingerprint, Anti-Rollback | `setup`, `firmware` | Setup/Firmware | BOOTSEL / Normal |
| GUI | 9 Tabs, Modus-Autoerkennung (3s), striktes Sperren mit Tooltip | — | alle | — |
| Firmware | Preflight, geführtes Flash mit TOTP (Flash nur BOOTSEL) | `firmware` | Firmware | BOOTSEL / Normal |
| Logs | Nur Audit-Log mit Hash-Chain-Status | `status audit`, `firmware audit` | Logs | immer |

## Schnellstart

```bash
pip install -e .
pico-hsm-cli --help                    # CLI
pico-hsm-gui                           # GUI (Dark, 9 Tabs)
# Ersteinrichtung
pico-hsm-cli init token
pico-hsm-cli pin change
pico-hsm-cli dkek status
```

## Doku

| Datei | Inhalt |
|---|---|
| [`docs/01-nutzung.md`](docs/01-nutzung.md) | Bedienung (CLI-Guide + GUI-Tabs) |
| [`docs/02-setup.md`](docs/02-setup.md) | Installation, Abhängigkeiten, Setup |
| [`docs/03-architektur-konzept.md`](docs/03-architektur-konzept.md) | Architektur-/Modulkonzept (Status abgehakt) |
| [`docs/06-testdokumentation.md`](docs/06-testdokumentation.md) | Test-Suite (344 Tests), Verfahren |
| [`docs/15-real-hardware-validation-checklist.md`](docs/15-real-hardware-validation-checklist.md) | HW-Validierung (echtes Board) |
| [`docs/20-roadmap.md`](docs/20-roadmap.md) | Zukünftige Features (u.a. Windows-Installer) |

## Status

- **344 Tests**, alle ohne Hardware (Mocks, Simulationen, echte
  Binaries) — siehe [`docs/06`](docs/06-testdokumentation.md).
- GUI komplett (9 Tabs: Start-Wizard + 7 CLI-Gruppen + Logs), CLI komplett; Probe-Doku in
  [`docs/15`](docs/15-real-hardware-validation-checklist.md).
- Lizenzen: siehe [`NOTICE.md`](NOTICE.md).
