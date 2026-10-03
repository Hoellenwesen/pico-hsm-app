<#
.SYNOPSIS
  Collects the Windows release artifacts into dist-release\.
.DESCRIPTION
  1. Runs the frontend build (npm run build).
  2. Builds the release binary (cargo build --release -p pico-hsm-app).
     NOTE: does NOT run `tauri build` (no installer, faster iteration).
     The NSIS setup must already exist from `npm run tauri build`
     (src-tauri/target/release/bundle/nsis/); if missing, it is skipped
     with a warning instead of failing.
  3. Produces in dist-release\ (versioned, normalized names):
       pico-hsm-app-<ver>-win-x64-setup.exe      (NSIS installer, copy)
       pico-hsm-app-<ver>-win-x64-portable.exe    (bare binary, copy)
       pico-hsm-app-<ver>-win-x64-portable.zip    (binary + README, fresh)
  Run from the repo root:  powershell -ExecutionPolicy Bypass -File tools\package-windows.ps1
#>
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

Write-Host "==> frontend build (npm run build) ..."
npm run build
if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }

Write-Host "==> release binary (cargo build --release) ..."
cargo build --release --manifest-path src-tauri/Cargo.toml
if ($LASTEXITCODE -ne 0) { throw "cargo build failed" }

$Version = (Get-Content -LiteralPath "src-tauri/tauri.conf.json" -Raw | ConvertFrom-Json).version
$Exe = Join-Path $Root "src-tauri/target/release/pico-hsm-app.exe"
if (-not (Test-Path -LiteralPath $Exe)) { throw "binary not found: $Exe" }

$OutDir = Join-Path $Root "dist-release"
New-Item -ItemType Directory -Path $OutDir -Force | Out-Null

# 1. Portable ZIP (fresh).
$Stage = Join-Path $OutDir "stage-pico-hsm-app"
if (Test-Path -LiteralPath $Stage) { Remove-Item -LiteralPath $Stage -Recurse -Force }
New-Item -ItemType Directory -Path $Stage | Out-Null
Copy-Item -LiteralPath $Exe -Destination (Join-Path $Stage "pico-hsm-app.exe")
Copy-Item -LiteralPath (Join-Path $Root "tools/README-Windows.txt") -Destination (Join-Path $Stage "README-Windows.txt")

$Zip = Join-Path $OutDir "pico-hsm-app-${Version}-win-x64-portable.zip"
if (Test-Path -LiteralPath $Zip) { Remove-Item -LiteralPath $Zip -Force }
Compress-Archive -Path (Join-Path $Stage "*") -DestinationPath $Zip
Remove-Item -LiteralPath $Stage -Recurse -Force

# 2. Portable EXE (copy, versioned name).
$PortableExe = Join-Path $OutDir "pico-hsm-app-${Version}-win-x64-portable.exe"
if (Test-Path -LiteralPath $PortableExe) { Remove-Item -LiteralPath $PortableExe -Force }
Copy-Item -LiteralPath $Exe -Destination $PortableExe

# 3. NSIS setup (copy, normalized name). Built by `npm run tauri build`,
#    not here — warn and continue if absent.
$SetupSrc = Get-ChildItem -LiteralPath (Join-Path $Root "src-tauri/target/release/bundle/nsis") -Filter "*-setup.exe" -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending | Select-Object -First 1
$SetupDst = Join-Path $OutDir "pico-hsm-app-${Version}-win-x64-setup.exe"
if ($null -eq $SetupSrc) {
  Write-Warning 'No NSIS setup found (run "npm run tauri build" first) — setup artifact skipped.'
} else {
  if (Test-Path -LiteralPath $SetupDst) { Remove-Item -LiteralPath $SetupDst -Force }
  Copy-Item -LiteralPath $SetupSrc.FullName -Destination $SetupDst
  Write-Host ("    setup source: {0}" -f $SetupSrc.Name)
}

function Show-Size($label, $path) {
  if (Test-Path -LiteralPath $path) {
    $mb = [math]::Round((Get-Item -LiteralPath $path).Length / 1MB, 1)
    Write-Host ("    {0}: {1} MB" -f $label, $mb)
  } else {
    Write-Host ("    {0}: MISSING" -f $label)
  }
}
Write-Host "==> dist-release contents:"
Show-Size "setup   " $SetupDst
Show-Size "portable" $PortableExe
Show-Size "zip     " $Zip
