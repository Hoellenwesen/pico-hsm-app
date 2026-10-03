Pico HSM App — portable Windows build
=====================================

Just run pico-hsm-app.exe. No installation, no admin rights, no dev server.
The frontend is embedded in the binary (custom-protocol build).
Settings (generation records, audit journal) live per Windows user.

Requirements
------------
1. Windows 10 (1809+) or Windows 11, 64-bit.
2. Microsoft Edge WebView2 runtime — preinstalled on current Windows.
   If the app refuses to start with a WebView2 error, install it free from:
   https://developer.microsoft.com/microsoft-edge/webview2/
   (Use the NSIS setup instead if you prefer automatic installation.)
3. Smart Card service running (default ON). If no reader appears:
   Win+R → services.msc → "Smartcard" → Start, start type Automatic.
4. Pico HSM board: standard CCID, no extra driver needed. If Windows asks
   for a driver, let Windows Update search once.

First-run notes
---------------
- No code signature (self-distributed 0.x): Windows SmartScreen warns once.
  "More info" → "Run anyway". Verify the SHA-256 from the release page.
- BOOTSEL flashing, PIN retries and DKEK shares behave exactly like the
  installed version — see the in-app help texts.

Troubleshooting
---------------
- "No smartcard readers found": service (3) + unplug/replug the board.
- Missing VC++ runtime (very old systems only): install "Microsoft Visual
  C++ Redistributable x64" once.
- App data: %APPDATA%\com.picohsm.desktop\ + browser localStorage of the app.
