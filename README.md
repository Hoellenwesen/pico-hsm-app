# Pico HSM App

Cross-platform manager for [Pico HSM](https://github.com/polhenarejos/pico-hsm)
tokens — Tauri 2 + React 19 + TypeScript frontend, Rust/PC/SC backend.
HSM-only scope (no FIDO/OpenPGP). Windows-first, Linux-capable.

### This is an alternative App to manage the Pico HSM device. I created it mostly for myself but feel free to use it at your own risk. If you need a proper developed, enterprise grade application then i would recommend using the official one provided by PicoKeys (https://www.picokeys.com/picokeyapp/).

## Features

- **Dashboard** — live device identity, memory, security options, PIN retries,
  session lock/unlock; offline gating with auto-return.
- **Keys** — on-device AES/RSA/EC generation (labels, usage counters, purpose
  restrictions), details, rename, single + bulk delete, CSR export.
- **Certificates** — per-key CVC/X.509 list with honest `Stored`/`None stored`
  states, X.509 import (validated, key-matched, read-back verified), PEM
  export/download, standalone CA delete.
- **CSR export** — PKCS#10 signed on-device (RSA/ECDSA), generation-record
  SPKI fallback, DN dialog.
- **Initialization** — User-PIN/SO-PIN setup, retry limit, DKEK options,
  wipe-aware confirm; init-state detection (`NotInitialized` probe).
- **Backup & Restore** — DKEK domains (XOR N-of-N shares, KCV proof,
  generate-once UI), key wrap/unwrap, versioned JSON bundles, safe restore
  (occupied IDs skipped, never overwritten).
- **Firmware** — BOOTSEL UF2 flashing with validation, family/MCU guards,
  optional SHA-256 (sidecar/paste), progress + flash log, tolerant reboot.
- **Logs** — app-side audit journal (mutations only, never secrets),
  filter/search/export/clear.
- **PIN management** — change/unblock, retries, session PIN (RAM-only,
  auto-cleared on disconnect), presence-aware flows.
- **i18n** — German + English (OS-locale default, sidebar switch);
  backend errors translated by code with server-text fallback.

![App Dashboard](/docs/img/Dashboard.png "App Dashboard")

## Quickstart

Prerequisites: Node 20+, Rust stable, WebView2 (Windows, usually present),
Smart Card service running, Pico HSM board connected.

```sh
npm install
npm run tauri dev      # dev loop (Vite on :1420)
npm run build          # frontend only
npm test               # vitest (frontend unit tests)
cargo test --manifest-path src-tauri/Cargo.toml --lib   # Rust unit tests
```

Release artifacts (Windows):

```powershell
npm run tauri build                            # NSIS setup -> bundle/nsis/
powershell -ExecutionPolicy Bypass -File tools\package-windows.ps1
# -> dist-release/: setup.exe, portable exe, portable zip
```

See `tools/README-Windows.txt` (requirements, SmartScreen, troubleshooting)
and `docs/acceptance.md` (test protocol, remaining work).

## Security notes

- Session PIN lives in RAM only, cleared on disconnect; never logged.
- Journal and storage never hold secrets: no PINs, shares, blobs, subjects.
- DKEK shares are shown once at generation and never stored; the DKEK itself
  never leaves the device (XOR N-of-N — lose one share, lose the DKEK).
- Destructive actions (delete, init wipe, flash, restore) always confirm.
- 0.x is unsigned: Windows SmartScreen warns once (“More info → Run anyway”);
  verify artifact hashes from the release page.

## Project layout

- `src/` — React frontend (`pages/`, `components/`, `hooks/useDevice.ts`,
  `lib/tauri.ts` command wrappers, `lib/i18n/` DE/EN catalogs).
- `src-tauri/` — Rust backend (`device.rs` HSM applet, `firmware.rs` UF2
  flasher, `lib.rs` command registry).
- `tools/` — Windows packaging script + portable README.
- `docs/` — `acceptance.md` (phases 0–7, remaining implementation, protocol).
