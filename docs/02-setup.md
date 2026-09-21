# Setup — pico-hsm-app installieren und einrichten

Gilt für Windows/macOS/Linux. Die App selbst ist ein reines Python-Paket
(`pico-hsm-app`); alle kryptografischen Operationen laufen über **externe
Programme** (`age`, `sc-hsm-tool`, `picotool`, OpenSC), die separat
installiert werden müssen.

App-Ablage (Audit-Log, Drill-Protokoll, GUI-Config, Wizard-State):
`~/.pico_hsm`, umleitbar per Umgebungsvariable `PICO_HSM_HOME`
(portable Nutzung; Tests isolieren damit das echte Home).

## 1. Python-Paket

```bash
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e .
```

Verifikation (muss ohne Fehler durchlaufen):

```bash
pip show pico-hsm-app          # Name + Version 0.1.0
pico-hsm-cli --help            # CLI-Einstiegspunkt
python -c "import gui.app; print('GUI-Einstieg ok')"
python -m pytest tests/ -q     # 344 Tests, alle ohne Hardware
```

## 2. Externe Abhängigkeiten (keine Python-Pakete)

| Programm | Zweck | Paket (Debian/Ubuntu) | Windows |
|---|---|---|---|
| `picotool` (mit USB-Support!) | Firmware-Preflight/Flash, OTP-Anzeige | selbst bauen — **nicht** die CMake-Auto-Download-Variante, die hat keinen USB-Support | selbst bauen (Visual Studio + libusb; ggf. Zadig-WinUSB-Treiber fürs Board im BOOTSEL-Modus) |
| `opensc-pkcs11.so`/`.dll` | PKCS#11-Modul für `python-pkcs11` | `opensc` | OpenSC-`.msi` vom GitHub-Release (legt `.dll` nach System32) |
| `pkcs11-tool` | PIN-Verwaltung | `opensc` | wie oben (OpenSC-`.msi`) |
| `sc-hsm-tool` | DKEK-Shares, Token-Init, Key-Wrap/Unwrap | `opensc` | wie oben (OpenSC-`.msi`) |
| `age`, `age-keygen` | Backup-Verschlüsselung (Empfänger-Modus) | `age` | Windows-Binary vom age-GitHub-Release |

```bash
sudo apt install opensc age
```

`picotool` mit USB-Support selbst bauen (so im Projekt gebaut und
getestet, siehe unten):

```bash
git clone --depth 1 https://github.com/raspberrypi/pico-sdk.git
cd pico-sdk && git submodule update --init --depth 1 && cd ..
git clone --depth 1 https://github.com/raspberrypi/picotool.git
cd picotool && mkdir build && cd build
cmake .. -DPICO_SDK_PATH=../../pico-sdk
make -j$(nproc)
# ./picotool ins PATH legen
```

Windows zusätzlich: Smartcard-Dienst starten (sonst sieht `pyscard`/
OpenSC keinen Reader) — Dienste-Verwaltung (`services.msc`):
`Smartcard` (SCardSvr) auf Automatisch + starten.

## 3. Firmware-Seal-/Flash-Toolchain (Board)

`picotool` v2.3.0 wurde aus dem Quellcode gebaut (pico-sdk + picotool
aus GitHub geholt), dabei zwei reale Bugs im ursprünglichen Entwurf
gefunden und korrigiert:

1. `picotool info` hat **kein** `--json` (existiert nicht) — Version/
   Rollback werden aus der Klartextzeile geparst.
2. `picotool verify <datei>` prüft **keine** Datei-Signatur, sondern
   vergleicht ein Gerät gegen eine Datei — schlägt ohne Gerät immer
   fehl. Die echte Prüfung läuft über `info -a` → Zeile
   `signature: verified/incorrect`.

Siehe `README.md` §2 und die Hardware-Validierungs-Checkliste in
`docs/15-real-hardware-validation-checklist.md` für den vollständigen
Build- und Testablauf.

## 4. Abhängigkeits-Quellen

`pyproject.toml` ist die EINZIGE Dependency-Quelle (keine
`requirements.txt`). GUI-Dependencies: `PySide6`, `PySide6-Fluent-Widgets`
(GPLv3 beachten, siehe `NOTICE.md`), dev: `pytest-qt`.
