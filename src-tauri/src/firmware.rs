//! Firmware update via BOOTSEL UF2 mass storage (Windows-first).
//!
//! Flow: validate UF2 -> check board (Pico MCU + HSM product) -> reboot to
//! BOOTSEL -> find `RPI-RP2` drive via `INFO_UF2.TXT` -> chunked copy with
//! progress events -> wait for drive-gone + board-back -> report old/new
//! version. UF2 flashing never touches keys (flash content is preserved).

use crate::device::{
    get_platform_info, get_version, reboot_device, scan_bootsel_drive, BootselDrive, DeviceError,
};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::io::Write;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter};

/// Max accepted UF2 size (3 MiB; Pico HSM images are well below 1 MiB).
pub const UF2_CAP_BYTES: usize = 3 * 1024 * 1024;
const UF2_BLOCK: usize = 512;
const UF2_MAGIC_0: u32 = 0x0A32_4655;
const UF2_MAGIC_1: u32 = 0x9E5D_5157;
const UF2_MAGIC_END: u32 = 0x0AB1_6F30;
const FLAG_FAMILY_ID: u32 = 0x0000_2000;
/// UF2 family ids per pico-sdk `boot/uf2.h` (verified against SDK v2.3.1).
const FAMILY_RP2040: u32 = 0xE48B_FF56;
/// Absolute-addressed blocks (boot-meta, no MCU of its own).
const FAMILY_ABSOLUTE: u32 = 0xE48B_FF57;
/// Data blocks (no MCU of its own).
const FAMILY_DATA: u32 = 0xE48B_FF58;
const FAMILY_RP2350_ARM_S: u32 = 0xE48B_FF59;
const FAMILY_RP2350_RISCV: u32 = 0xE48B_FF5A;
const FAMILY_RP2350_ARM_NS: u32 = 0xE48B_FF5B;
const COPY_CHUNK: usize = 65536;
const WAIT_DRIVE_SECS: u64 = 60;
/// Generous: the user may need to power-cycle the board mid-wait.
const WAIT_BACK_SECS: u64 = 300;

#[derive(Debug, Serialize, Clone)]
pub struct Uf2Info {
    pub path: String,
    pub bytes: usize,
    pub blocks: u32,
    /// Family id as 8 uppercase hex chars, e.g. "E48BFF56".
    pub family_hex: String,
    /// "RP2040" | "RP2350" | "unknown".
    pub mcu: String,
    /// Core of the payload family: "ARM" | "RISC-V" | "unknown".
    /// A RISC-V image on an ARM-booted board is silently ignored by the
    /// ROM bootloader (stays in BOOTSEL) — hence surfaced, not just checked.
    pub core: String,
    /// SHA-256 of the file as lowercase hex (always computed).
    pub sha256_hex: String,
    /// Expected hash from a `<file>.sha256[.sum]` sidecar, if present.
    pub sidecar_hash: Option<String>,
}

#[derive(Debug, Serialize, Clone)]
pub struct FlashProgress {
    /// waiting-drive | flashing | waiting-reboot | done.
    pub phase: String,
    pub done_bytes: usize,
    pub total_bytes: usize,
}

#[derive(Debug, Serialize, Clone)]
pub struct FlashResult {
    pub old_version: String,
    pub new_version: String,
}

fn u32le(b: &[u8]) -> u32 {
    (b[0] as u32) | ((b[1] as u32) << 8) | ((b[2] as u32) << 16) | ((b[3] as u32) << 24)
}

fn mcu_for_family(family: u32) -> &'static str {
    match family {
        FAMILY_RP2040 => "RP2040",
        FAMILY_RP2350_ARM_S | FAMILY_RP2350_RISCV | FAMILY_RP2350_ARM_NS => "RP2350",
        // ABSOLUTE/DATA carry no MCU — known but not board-matchable.
        _ => "unknown",
    }
}

/// Core variant of a known family: the rescue applet only reports the MCU
/// (RP2350), never ARM vs RISC-V — so the image core stays user-verified.
fn core_for_family(family: u32) -> &'static str {
    match family {
        FAMILY_RP2040 | FAMILY_RP2350_ARM_S | FAMILY_RP2350_ARM_NS => "ARM",
        FAMILY_RP2350_RISCV => "RISC-V",
        _ => "unknown",
    }
}

fn family_known(family: u32) -> bool {
    matches!(
        family,
        FAMILY_RP2040
            | FAMILY_ABSOLUTE
            | FAMILY_DATA
            | FAMILY_RP2350_ARM_S
            | FAMILY_RP2350_RISCV
            | FAMILY_RP2350_ARM_NS
    )
}

/// Validate raw UF2 bytes and return (file block count, main family id).
/// Layout per UF2 spec: magic0@0 magic1@4 flags@8 addr@12 size@16 no@20
/// total@24 family@28 data@32 magicEnd@508.
///
/// Real elf2uf2 images are looser than the textbook: block 0 may be a
/// single boot-meta block with its own (family,total) group (seen:
/// `E48BFF57/2` + 1413 payload blocks `E48BFF59/1413`). Rules:
/// - every block: magic trio, 256-byte payload, family-id flag, known family;
/// - within each (family,total) group: numbers unique and below total;
/// - at least one complete group (count == total, numbers 0..total-1);
/// - the reported family comes from the largest complete group.
fn parse_uf2_bytes(data: &[u8]) -> Result<(u32, u32), DeviceError> {
    let bad = |msg: String| DeviceError::new("BadUf2", msg, "Pick a Pico HSM .uf2 firmware image.");
    if data.is_empty() || data.len() % UF2_BLOCK != 0 {
        return Err(bad(format!("Size {} is not a multiple of 512 bytes", data.len())));
    }
    // (family,total) -> sorted block numbers.
    let mut groups: std::collections::BTreeMap<(u32, u32), Vec<u32>> = Default::default();
    for block in data.chunks_exact(UF2_BLOCK) {
        if u32le(&block[0..4]) != UF2_MAGIC_0
            || u32le(&block[4..8]) != UF2_MAGIC_1
            || u32le(&block[508..512]) != UF2_MAGIC_END
        {
            return Err(bad("A block has bad UF2 magic numbers".to_string()));
        }
        let flags = u32le(&block[8..12]);
        if flags & FLAG_FAMILY_ID == 0 {
            return Err(bad("A block carries no family id (not a Pico UF2)".to_string()));
        }
        let family = u32le(&block[28..32]);
        if !family_known(family) {
            return Err(bad(format!("Block targets unknown family {family:08X}")));
        }
        if u32le(&block[16..20]) != 256 {
            return Err(bad("A block has unexpected payload size (need 256)".to_string()));
        }
        let num = u32le(&block[20..24]);
        let total = u32le(&block[24..28]);
        if total == 0 || num >= total {
            return Err(bad(format!("Block number {num} out of range (total {total})")));
        }
        groups.entry((family, total)).or_default().push(num);
    }
    // Largest complete group wins (count == total, numbers exactly 0..total-1).
    let mut best: Option<(u32, u32)> = None;
    for ((family, total), mut nos) in groups {
        nos.sort_unstable();
        nos.dedup();
        if nos.len() as u32 == total && nos.iter().enumerate().all(|(i, &n)| n == i as u32) {
            if best.is_none_or(|(_, t)| total > t) {
                best = Some((family, total));
            }
        }
    }
    let (family, _total) = best
        .ok_or_else(|| bad("No complete block sequence found (numbers must cover 0..total-1)".to_string()))?;
    Ok((data.len() as u32 / UF2_BLOCK as u32, family))
}

/// Normalize a user/supplied hash: optional `0x`, 64 hex chars, lowercase.
/// Accepts `<hex>  <filename>` lines (uses the first token) and bare hex.
fn normalize_hash(input: &str) -> Option<String> {
    let first = input.split_whitespace().next()?;
    let hex = first.strip_prefix("0x").or_else(|| first.strip_prefix("0X")).unwrap_or(first);
    if hex.len() != 64 || !hex.bytes().all(|b| b.is_ascii_hexdigit()) {
        return None;
    }
    Some(hex.to_lowercase())
}

/// Look for `<path>.sha256` / `<path>.sha256sum` next to the UF2 file and
/// extract the expected hash. Absent/unparsable sidecar -> None (no error:
/// hash verification stays optional).
fn read_sidecar_hash(uf2_path: &str) -> Option<String> {
    for suffix in [".sha256", ".sha256sum"] {
        let sidecar = format!("{uf2_path}{suffix}");
        if let Ok(body) = std::fs::read_to_string(&sidecar) {
            // Standard format is one line; scan all lines for the first hash.
            for line in body.lines() {
                // Skip checksum lines that clearly belong to other files?
                // No: take the first valid 64-hex token (sidecars are per-file).
                if let Some(h) = normalize_hash(line) {
                    return Some(h);
                }
            }
        }
    }
    None
}

/// SHA-256 hex of bytes (lowercase).
fn sha256_hex(data: &[u8]) -> String {
    let mut h = Sha256::new();
    h.update(data);
    hex::encode(h.finalize())
}

/// Verify a UF2 file against an expected SHA-256 hex string.
/// Runs before any reboot — a mismatch never touches the board.
#[tauri::command]
pub fn verify_uf2_hash(path: String, expected_hex: String) -> Result<String, DeviceError> {
    let expected = normalize_hash(&expected_hex).ok_or_else(|| {
        DeviceError::new(
            "BadHash",
            "Expected hash must be 64 hex characters".to_string(),
            "Paste the SHA-256 from the firmware release notes.",
        )
    })?;
    let data = std::fs::read(&path).map_err(|e| {
        DeviceError::new(
            "BadFile",
            format!("Cannot read file: {e}"),
            "Pick the file again.",
        )
    })?;
    let actual = sha256_hex(&data);
    // Constant-time compare (hashes are not secret, but cheap to do right).
    let mut diff = 0u8;
    for (a, b) in actual.bytes().zip(expected.bytes()) {
        diff |= a ^ b;
    }
    if diff != 0 {
        return Err(DeviceError::new(
            "HashMismatch",
            format!("SHA-256 mismatch: file is {actual}, expected {expected}"),
            "Do not flash this file — re-download it.",
        ));
    }
    Ok(actual)
}

/// Validate a UF2 firmware file (read-only): magic, sequence, family, cap.
#[tauri::command]
pub fn parse_uf2_file(path: String) -> Result<Uf2Info, DeviceError> {
    let data = std::fs::read(&path).map_err(|e| {
        DeviceError::new(
            "BadFile",
            format!("Cannot read file: {e}"),
            "Pick a readable .uf2 firmware image.",
        )
    })?;
    if data.len() > UF2_CAP_BYTES {
        return Err(DeviceError::new(
            "BadFile",
            format!("File is {} bytes (cap 3 MiB)", data.len()),
            "Pick the correct Pico HSM .uf2 image.",
        ));
    }
    let (blocks, family) = parse_uf2_bytes(&data)?;
    Ok(Uf2Info {
        path: path.clone(),
        bytes: data.len(),
        blocks,
        family_hex: format!("{family:08X}"),
        mcu: mcu_for_family(family).to_string(),
        core: core_for_family(family).to_string(),
        sha256_hex: sha256_hex(&data),
        sidecar_hash: read_sidecar_hash(&path),
    })
}

/// Find the BOOTSEL mass-storage drive via INFO_UF2.TXT (Windows-first).
#[tauri::command]
pub fn find_bootsel_drive() -> Result<BootselDrive, DeviceError> {
    if !cfg!(target_os = "windows") {
        return Err(DeviceError::new(
            "UnsupportedOs",
            "Automatic BOOTSEL drive detection is Windows-only in this version".to_string(),
            "Reboot to BOOTSEL and copy the .uf2 file manually.",
        ));
    }
    scan_bootsel_drive().ok_or_else(|| {
        DeviceError::new(
            "NoBootsel",
            "No BOOTSEL drive found (expected RPI-RP2 with INFO_UF2.TXT)".to_string(),
            "Reboot the device into BOOTSEL mode and wait for the drive.",
        )
    })
}

/// Full flash: validate -> board guards -> BOOTSEL reboot -> copy with
/// progress events -> wait for board-back -> old/new version report.
#[tauri::command]
pub fn flash_uf2(app: AppHandle, reader: String, path: String) -> Result<FlashResult, DeviceError> {
    let emit = |phase: &str, done: usize, total: usize| {
        let _ = app.emit(
            "flash-progress",
            FlashProgress {
                phase: phase.to_string(),
                done_bytes: done,
                total_bytes: total,
            },
        );
    };
    // 1. File validation (also re-reads bytes for the copy).
    let info = parse_uf2_file(path.clone())?;
    let data = std::fs::read(&path).map_err(|e| {
        DeviceError::new(
            "BadFile",
            format!("Cannot re-read file for flashing: {e}"),
            "Pick the file again.",
        )
    })?;
    // 2. Board guards: Pico MCU + HSM product + family match.
    let platform = get_platform_info(reader.clone())?;
    if platform.product != "HSM" {
        return Err(DeviceError::new(
            "RefusedFlash",
            format!("Device product is {} (need HSM)", platform.product),
            "This flasher only serves Pico HSM boards.",
        ));
    }
    if platform.platform != "RP2040" && platform.platform != "RP2350" {
        return Err(DeviceError::new(
            "RefusedFlash",
            format!("MCU is {} (need RP2040/RP2350)", platform.platform),
            "ESP32 targets use a different flashing method.",
        ));
    }
    if info.mcu != platform.platform {
        return Err(DeviceError::new(
            "FamilyMismatch",
            format!("UF2 targets {} but the board is {}", info.mcu, platform.platform),
            "Flashing the wrong image leaves the board unbootable until reflashed — aborted.",
        ));
    }
    let old_version = get_version(reader.clone()).map(|v| v.display).unwrap_or_else(|_| "?".to_string());
    // 3. Reboot to BOOTSEL.
    reboot_device(reader.clone(), true)?;
    // 4. Wait for the BOOTSEL drive.
    emit("waiting-drive", 0, info.bytes);
    let deadline = Instant::now() + Duration::from_secs(WAIT_DRIVE_SECS);
    let drive = loop {
        if let Some(d) = scan_bootsel_drive() {
            break d;
        }
        if Instant::now() > deadline {
            return Err(DeviceError::new(
                "BootselTimeout",
                "BOOTSEL drive did not appear within 60 s".to_string(),
                "Hold BOOTSEL while plugging in, then retry. Keys are untouched.",
            ));
        }
        std::thread::sleep(Duration::from_millis(500));
    };
    // 5. Chunked copy with progress.
    // Hardening (the board stayed in BOOTSEL once despite a good image):
    // - 8.3-safe name (LFN edge cases on the tiny FAT are not our problem);
    // - sync_all forces OS buffers to the device (close alone does not);
    // - size check proves the FAT entry matches what we sent;
    // - settle delay lets the bootloader consume the closed file.
    emit("flashing", 0, info.bytes);
    let dest = format!("{}\\picohsm.uf2", drive.drive);
    let mut out = std::fs::File::create(&dest).map_err(|e| {
        DeviceError::new(
            "FlashWrite",
            format!("Cannot write to BOOTSEL drive: {e}"),
            "Check the drive is writable, then retry.",
        )
    })?;
    let mut done = 0;
    for chunk in data.chunks(COPY_CHUNK) {
        out.write_all(chunk).map_err(|e| {
            DeviceError::new(
                "FlashWrite",
                format!("Write failed at byte {done}: {e}"),
                "The board stays in BOOTSEL — retry the copy.",
            )
        })?;
        done += chunk.len();
        emit("flashing", done, info.bytes);
    }
    out.flush().map_err(|e| {
        DeviceError::new(
            "FlashWrite",
            format!("Flush failed: {e}"),
            "The board stays in BOOTSEL — retry.",
        )
    })?;
    // FlushFileBuffers: push OS caches out to the USB device now.
    if let Err(e) = out.sync_all() {
        return Err(DeviceError::new(
            "FlashWrite",
            format!("OS sync to BOOTSEL drive failed: {e}"),
            "The transfer may be incomplete — the board stays in BOOTSEL. Retry.",
        ));
    }
    drop(out);
    let written = std::fs::metadata(&dest).map(|m| m.len()).unwrap_or(0);
    if written != info.bytes as u64 {
        return Err(DeviceError::new(
            "FlashWrite",
            format!("Size on drive is {written} bytes, expected {}", info.bytes),
            "The transfer did not land completely — retry. The board stays in BOOTSEL.",
        ));
    }
    std::thread::sleep(Duration::from_secs(3));
    // 6. Wait for reboot: drive gone AND version readable again.
    // Sticky-BOOTSEL case: after a software BOOTSEL entry the ROM may loop
    // back to BOOTSEL instead of booting (watchdog scratch survives watchdog
    // resets, not power cycles). Signature: our file is GONE from the drive
    // (consumed) while the drive persists — then no mass-storage command can
    // reboot it, and the user must power-cycle once. Guide instead of waiting
    // blindly; the existing drive-gone + version check below finishes the
    // flow after the replug.
    emit("waiting-reboot", done, info.bytes);
    let deadline = Instant::now() + Duration::from_secs(WAIT_BACK_SECS);
    let mut power_cycle_hinted = false;
    let new_version = loop {
        let drive_gone = scan_bootsel_drive().is_none();
        if drive_gone {
            if let Ok(v) = get_version(reader.clone()) {
                break v.display;
            }
        } else if !power_cycle_hinted && std::fs::metadata(&dest).is_err() {
            // File consumed (RAM drive clears on reboot) but BOOTSEL persists:
            // the image was taken, the return trip needs a power cycle.
            power_cycle_hinted = true;
            emit("power-cycle", done, info.bytes);
        }
        if Instant::now() > deadline {
            let drive = scan_bootsel_drive();
            return Err(if drive.is_some() {
                DeviceError::new(
                    "StuckBootsel",
                    "Board still in BOOTSEL — the transfer did not complete or the image was ignored".to_string(),
                    "Unplug the board (or press RESET) to boot the previous firmware. If it repeats, copy the same file via Explorer once: if that reboots, the app transfer is at fault — report it.",
                )
            } else {
                DeviceError::new(
                    "RebootTimeout",
                    "Board did not come back after flashing".to_string(),
                    "Unplug/replug the board. If it stays dark, reflash via BOOTSEL manually.",
                )
            });
        }
        std::thread::sleep(Duration::from_secs(1));
    };
    emit("done", info.bytes, info.bytes);
    Ok(FlashResult { old_version, new_version })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::device::parse_info_uf2;

    /// Build a synthetic UF2 image: `blocks` 512-byte blocks for `family`.
    /// A single block can be overridden via `patch` (block index, field offset, value).
    fn synth_uf2(blocks: u32, family: u32) -> Vec<u8> {
        synth_uf2_mixed(&[(family, blocks)])
    }

    /// Build an image from (family, count) groups, each numbered 0..count-1
    /// (mirrors real elf2uf2 output with a lone boot-meta block).
    fn synth_uf2_mixed(groups: &[(u32, u32)]) -> Vec<u8> {
        let mut out = Vec::new();
        for &(family, count) in groups {
            for n in 0..count {
                let mut b = vec![0u8; 512];
                b[0..4].copy_from_slice(&UF2_MAGIC_0.to_le_bytes());
                b[4..8].copy_from_slice(&UF2_MAGIC_1.to_le_bytes());
                b[8..12].copy_from_slice(&FLAG_FAMILY_ID.to_le_bytes());
                b[12..16].copy_from_slice(&[0u8; 4]); // target addr
                b[16..20].copy_from_slice(&256u32.to_le_bytes()); // payload size
                b[20..24].copy_from_slice(&n.to_le_bytes()); // block number
                b[24..28].copy_from_slice(&count.to_le_bytes()); // block count
                b[28..32].copy_from_slice(&family.to_le_bytes());
                b[508..512].copy_from_slice(&UF2_MAGIC_END.to_le_bytes());
                out.extend_from_slice(&b);
            }
        }
        out
    }

    #[test]
    fn uf2_magic_byte_patterns() {
        // Regression: exact on-wire bytes per UF2 spec ("UF2\n" + end marker).
        // A typo here rejects every real file while self-built tests still pass.
        assert_eq!(&UF2_MAGIC_0.to_le_bytes(), b"UF2\n");
        assert_eq!(&UF2_MAGIC_1.to_le_bytes(), &[0x57, 0x51, 0x5D, 0x9E]);
        assert_eq!(&UF2_MAGIC_END.to_le_bytes(), &[0x30, 0x6F, 0xB1, 0x0A]);
    }

    #[test]
    fn uf2_valid_parses() {
        let img = synth_uf2(4, FAMILY_RP2040);
        let (blocks, family) = parse_uf2_bytes(&img).expect("valid parses");
        assert_eq!(blocks, 4);
        assert_eq!(family, FAMILY_RP2040);
        assert_eq!(mcu_for_family(family), "RP2040");
        assert_eq!(mcu_for_family(FAMILY_RP2350_ARM_S), "RP2350");
        assert_eq!(mcu_for_family(FAMILY_RP2350_RISCV), "RP2350");
        assert_eq!(mcu_for_family(FAMILY_RP2350_ARM_NS), "RP2350");
        assert_eq!(mcu_for_family(FAMILY_ABSOLUTE), "unknown");
        assert_eq!(mcu_for_family(0xDEAD_BEEF), "unknown");
        assert_eq!(core_for_family(FAMILY_RP2040), "ARM");
        assert_eq!(core_for_family(FAMILY_RP2350_ARM_S), "ARM");
        assert_eq!(core_for_family(FAMILY_RP2350_RISCV), "RISC-V");
        assert_eq!(core_for_family(FAMILY_ABSOLUTE), "unknown");
        assert_eq!(core_for_family(0xDEAD_BEEF), "unknown");
    }

    #[test]
    fn uf2_real_world_shape_parses() {
        // Shape of a real pico-sdk image: lone ABSOLUTE boot-meta block +
        // complete payload sequence (RP2350 ARM-S here, as built for pico2).
        let img = synth_uf2_mixed(&[(FAMILY_ABSOLUTE, 1), (FAMILY_RP2350_ARM_S, 5)]);
        let (blocks, family) = parse_uf2_bytes(&img).expect("real-world shape parses");
        assert_eq!(blocks, 6);
        assert_eq!(family, FAMILY_RP2350_ARM_S);
        assert_eq!(mcu_for_family(family), "RP2350");
        // A lone block claiming a larger sequence is not a flashable image.
        let mut lone = synth_uf2_mixed(&[(FAMILY_ABSOLUTE, 1)]);
        lone[24..28].copy_from_slice(&2u32.to_le_bytes());
        assert!(parse_uf2_bytes(&lone).is_err());
    }

    #[test]
    fn uf2_rejects_garbage() {
        let img = synth_uf2(4, FAMILY_RP2040);
        // Not a multiple of 512.
        assert!(parse_uf2_bytes(&img[..100]).is_err());
        assert!(parse_uf2_bytes(&[]).is_err());
        // Bad magic.
        let mut bad = img.clone();
        bad[0] = 0xFF;
        assert!(parse_uf2_bytes(&bad).is_err());
        let mut bad = img.clone();
        bad[511] = 0xFF;
        assert!(parse_uf2_bytes(&bad).is_err());
        // Block-number gap.
        let mut gap = img.clone();
        gap[512 + 20..512 + 24].copy_from_slice(&9u32.to_le_bytes());
        assert!(parse_uf2_bytes(&gap).is_err());
        // Truncated count field (claims 5, has 4).
        let mut cnt = img.clone();
        for b in cnt.chunks_exact_mut(512) {
            b[24..28].copy_from_slice(&5u32.to_le_bytes());
        }
        assert!(parse_uf2_bytes(&cnt).is_err());
        // Missing family flag.
        let mut fl = img.clone();
        fl[8..12].copy_from_slice(&0u32.to_le_bytes());
        assert!(parse_uf2_bytes(&fl).is_err());
        // Unknown family.
        assert!(parse_uf2_bytes(&synth_uf2(2, 0xDEAD_BEEF)).is_err());
    }

    #[test]
    fn info_uf2_model_line() {
        let body = "UF2 Bootloader v2.0\r\nModel: Raspberry Pi RP2040 Device\r\nBoard-ID: ABC123\r\n";
        assert_eq!(
            parse_info_uf2(body),
            Some("Raspberry Pi RP2040 Device".to_string())
        );
        assert_eq!(parse_info_uf2("no model here\n"), None);
    }

    #[test]
    fn sha256_nist_vector() {
        // NIST: SHA-256("abc").
        assert_eq!(
            sha256_hex(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }

    #[test]
    fn normalize_hash_shapes() {
        let h = "BA7816BF8F01CFEA414140DE5DAE2223B00361A396177A9CB410FF61F20015AD";
        assert_eq!(normalize_hash(h), Some(h.to_lowercase()));
        assert_eq!(normalize_hash(&format!("0x{h}")), Some(h.to_lowercase()));
        assert_eq!(normalize_hash(&format!("{h}  firmware.uf2")), Some(h.to_lowercase()));
        assert_eq!(normalize_hash("  abc123  other.bin\n"), None);
        assert_eq!(normalize_hash("zz7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"), None);
        assert_eq!(normalize_hash(""), None);
    }
}
