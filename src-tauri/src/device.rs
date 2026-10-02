//! PC/SC transport + Pico HSM commands (queries, PIN, keys, certs, DKEK, init).
//!
//! Design: every command establishes a fresh PC/SC context and connects
//! per call. Slightly more overhead than a held handle, but avoids all
//! lifetime/thread-safety issues with Tauri's async command dispatch.
//!
//! Protocol facts (verified against firmware/SDK sources + pypicohsm
//! behavior, re-implemented here — no AGPL code copied):
//! - SELECT HSM applet: `00 A4 04 04 0B E82B0601040181C31F0201`, FCP carries
//!   proprietary tag `85 05 [opts_hi opts_lo FF ver_major ver_minor]`.
//! - SELECT rescue applet: `00 A4 04 00 08 A0583FC19B7E4F21` ->
//!   `[mcu product ver_major ver_minor ...board_id]` with MCU 0=RP2040,
//!   1=RP2350, 2=ESP32-S3, 3=EMULATION, 4=ESP32-S2 and product 0=UNKNOWN,
//!   1=HSM, 2=FIDO, 3=OPENPGP. Board id bytes render as uppercase hex
//!   (same as the USB serial string).
//! - Rescue READs (`80 1E <what> 00`, no auth): PHY=0x01 (commissioning
//!   TLV), FLASH=0x02 (5x u32 BE free/used/total/files/flash_size, plus
//!   fw_size on Pico targets), SECURE=0x03 ([boot_enabled locked bootkey]).
//! - Rescue REBOOT (`80 1F <mode> 00`): P1=1 BOOTSEL (Pico only, needs user
//!   presence), P1=0 normal reboot.
//! - Rescue clock: GET `80 1E 04 01` (8 bytes like DATETIME, 6985 when never
//!   set), SET `80 1C 02 01` + 8 bytes. No PIN needed (no auth gate in the
//!   rescue handler).
//! - Vendor extras via `80 64 <CMD> 00`: DYNOPS=0x06 (read/write, auth for
//!   write), SECURE-LOCK=0x3A (ECDH ceremony). Defined but NOT handled by the
//!   firmware dispatcher (answers 6A86): PHY=0x1B, REBOOT=0xFB, OTP=0x4C —
//!   PHY lives in rescue (`80 1E/1C 01`), reboot in rescue (`80 1F`).
//!   NOTE: CMD_DATETIME=0x0A was REMOVED from the HSM applet in this fork
//!   (answers 6A86 after auth, 6982 before) — the clock now lives in the
//!   rescue applet (see below). CMD_MEMORY=0x05 is likewise unimplemented.
//! - PIN retries without auth: `00 20 00 81` -> `63 CX` (X retries left).
//! - Serial (HSM identity): READ BINARY `00 B1 2F 02` (+ offset TLV
//!   `54 02 00 00`) returns a CVC blob; CHR (tag `5F 20`) holds the device
//!   id string (same value `pkcs15-tool -D` shows as token serial).

use pcsc::{Context, Protocol, Protocols, Scope, ShareMode};
use serde::Serialize;
use std::ffi::CString;
use std::sync::Mutex;
use std::time::{Duration, Instant};

/// Serializes ALL PC/SC traffic to one in-flight transaction.
///
/// Background: the UI polls several commands in parallel and a reboot makes
/// the USB device vanish mid-transaction. Interleaved SCardTransmit calls
/// against the single-threaded CCID firmware / vendor driver caused a native
/// stack-buffer-overrun crash (0xC0000409). One transaction at a time avoids it.
static PCSC_LOCK: Mutex<()> = Mutex::new(());

fn lock_pcsc() -> std::sync::MutexGuard<'static, ()> {
    PCSC_LOCK.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
}

const HSM_SELECT_APDU: &[u8] = &[
    0x00, 0xA4, 0x04, 0x04, 0x0B, 0xE8, 0x2B, 0x06, 0x01, 0x04, 0x01, 0x81, 0xC3, 0x1F, 0x02, 0x01,
    0x00,
];
/// SELECT rescue applet (returns MCU/product/version/board-id, no auth needed).
const RESCUE_SELECT_APDU: &[u8] = &[
    0x00, 0xA4, 0x04, 0x00, 0x08, 0xA0, 0x58, 0x3F, 0xC1, 0x9B, 0x7E, 0x4F, 0x21, 0x00,
];
const GET_RESPONSE_APDU: &[u8] = &[0x00, 0xC0, 0x00, 0x00];

#[derive(Debug, Serialize, Clone)]
pub struct DeviceError {
    pub code: String,
    pub message: String,
    pub hint: String,
    /// True when the operation needs a User-PIN login first (SW 6982).
    /// The frontend uses this to prompt for the PIN instead of showing an error.
    pub auth_required: bool,
}

impl DeviceError {
    pub(crate) fn new(code: &str, message: String, hint: &str) -> Self {
        Self {
            code: code.to_string(),
            message,
            hint: hint.to_string(),
            auth_required: false,
        }
    }

    /// Signal for the frontend to prompt for the User-PIN instead of erroring.
    fn auth_required(code: &str, message: String) -> Self {
        Self {
            code: code.to_string(),
            message,
            hint: "Log in with the User-PIN first.".to_string(),
            auth_required: true,
        }
    }

    /// Prefix the message with the failing step (VERIFY/LIST/GENERATE/LABEL)
    /// so bug reports pinpoint where a multi-APDU command broke.
    fn at_step(mut self, step: &str) -> Self {
        self.message = format!("[{step}] {}", self.message);
        self
    }
}

fn pcsc_unavailable_hint() -> &'static str {
    if cfg!(target_os = "windows") {
        "Check that the 'Smart Card' Windows service is running."
    } else {
        "Install pcscd (e.g. `sudo apt install pcscd libpcsclite1`) and start it: `sudo systemctl start pcscd`."
    }
}

fn establish() -> Result<Context, DeviceError> {
    Context::establish(Scope::User).map_err(|e| {
        DeviceError::new(
            "PcscUnavailable",
            format!("Failed to establish PC/SC context: {e}"),
            pcsc_unavailable_hint(),
        )
    })
}

#[derive(Debug, Serialize, Clone)]
pub struct CardStatus {
    pub reader: String,
    pub atr_hex: String,
    pub protocol: String,
}

#[derive(Debug, Serialize, Clone)]
pub struct TransmitResult {
    pub data_hex: String,
    pub sw1: u8,
    pub sw2: u8,
    pub sw_hex: String,
}

#[derive(Debug, Serialize, Clone)]
pub struct RtcTime {
    /// ISO-ish display string, e.g. "2026-09-28 16:05:11".
    pub display: String,
    pub year: u16,
    pub month: u8,
    pub day: u8,
    pub hour: u8,
    pub minute: u8,
    pub second: u8,
}

#[derive(Debug, Serialize, Clone)]
pub struct FirmwareVersion {
    pub display: String,
    pub major: u8,
    pub minor: u8,
    /// Raw device-options word from the SELECT FCP (tag 0x85).
    pub opts_hex: String,
}

#[derive(Debug, Serialize, Clone)]
pub struct PinStatus {
    /// Remaining retries, or -1 when no PIN is required, -2 when blocked.
    pub retries: i8,
    pub blocked: bool,
    pub sw_hex: String,
}

#[derive(Debug, Serialize, Clone)]
pub struct SerialInfo {
    pub serial: String,
    /// Length of the raw CVC blob the serial was extracted from.
    pub raw_len: usize,
}

#[derive(Debug, Serialize, Clone)]
pub struct PlatformInfo {
    pub platform: String,
    pub product: String,
    pub version: String,
    /// Board id bytes as uppercase hex (USB serial string equivalent).
    pub board_hex: String,
    pub raw_hex: String,
}

#[derive(Debug, Serialize, Clone)]
pub struct FlashInfo {
    pub free_bytes: u32,
    pub used_bytes: u32,
    pub total_bytes: u32,
    pub file_count: u32,
    /// Flash chip size in bytes.
    pub flash_bytes: u32,
    /// Firmware binary size; only reported on Pico targets (24-byte response).
    pub firmware_bytes: Option<u32>,
    pub raw_hex: String,
}

#[derive(Debug, Serialize, Clone)]
pub struct SecureInfo {
    pub secure_boot: bool,
    pub locked: bool,
    pub boot_key: u8,
}

/// Max response bytes per transmit: 64 KiB + SW (extended APDU responses,
/// e.g. large EC public-key blobs, exceed the pcsc 264-byte default which
/// broke EC generation with big curves). Heap buffer, no stack pressure.
const MAX_RAPDU: usize = 65538;

fn transmit_raw(card: &pcsc::Card, apdu: &[u8]) -> Result<TransmitResult, DeviceError> {
    let mut rapdu_buf = vec![0u8; MAX_RAPDU];
    let rapdu = card.transmit(apdu, &mut rapdu_buf).map_err(|e| {
        DeviceError::new(
            "TransmitFailed",
            format!("APDU transmit failed: {e}"),
            "Check the device is still connected and no other app holds an exclusive lock.",
        )
    })?;
    if rapdu.len() < 2 {
        return Err(DeviceError::new(
            "ShortResponse",
            format!("Card returned only {} byte(s), expected data + SW1/SW2", rapdu.len()),
            "Retry; if persistent, capture the trace for a bug report.",
        ));
    }
    let (data, sw) = rapdu.split_at(rapdu.len() - 2);
    Ok(TransmitResult {
        data_hex: hex::encode(data).to_uppercase(),
        sw1: sw[0],
        sw2: sw[1],
        sw_hex: format!("{:02X}{:02X}", sw[0], sw[1]),
    })
}

/// Transmit + follow `61 XX` (bytes remaining) with GET RESPONSE until all
/// data is collected. A `6C XX` (wrong length, resend with XX) is honored
/// once per chain. Returns concatenated data + final SW.
fn transmit_on_card(card: &pcsc::Card, apdu: &[u8]) -> Result<TransmitResult, DeviceError> {
    let mut out = transmit_raw(card, apdu)?;
    let mut data_hex = out.data_hex.clone();
    let mut guard = 0;
    let mut fixed_6c = false;
    loop {
        if out.sw1 == 0x61 && guard < 64 {
            guard += 1;
            let le = if out.sw2 == 0 { 256 } else { out.sw2 as usize };
            let mut get_resp = GET_RESPONSE_APDU.to_vec();
            get_resp.push(if le == 256 { 0 } else { le as u8 });
            out = transmit_raw(card, &get_resp)?;
            data_hex.push_str(&out.data_hex);
            continue;
        }
        if out.sw1 == 0x6C && !fixed_6c && apdu.len() >= 5 {
            // Our short APDUs always end in an explicit Le byte — swap in
            // the card's expected length and resend once. (Case-1 APDUs
            // without Le are 4 bytes and excluded by the length guard.)
            fixed_6c = true;
            let mut retry = apdu.to_vec();
            if let Some(le) = retry.last_mut() {
                *le = out.sw2;
            }
            out = transmit_raw(card, &retry)?;
            data_hex = out.data_hex.clone();
            continue;
        }
        break;
    }
    out.data_hex = data_hex;
    Ok(out)
}

/// SELECT the HSM applet; returns raw FCP bytes on success.
fn select_hsm(card: &pcsc::Card) -> Result<Vec<u8>, DeviceError> {
    let sel = transmit_on_card(card, HSM_SELECT_APDU)?;
    if sel.sw_hex != "9000" {
        return Err(DeviceError::new(
            "SelectFailed",
            format!("SELECT HSM applet failed with SW={}", sel.sw_hex),
            "The card does not answer as Pico HSM (wrong applet / device?).",
        ));
    }
    hex::decode(&sel.data_hex).map_err(|e| {
        DeviceError::new(
            "SelectParse",
            format!("SELECT response is not hex: {e}"),
            "Retry; if persistent, capture the trace for a bug report.",
        )
    })
}

fn with_card<T>(reader: &str, f: impl FnOnce(&pcsc::Card) -> Result<T, DeviceError>) -> Result<T, DeviceError> {
    let _guard = lock_pcsc();
    let ctx = establish()?;
    let c_reader = CString::new(reader).map_err(|e| {
        DeviceError::new("BadReader", format!("Invalid reader name: {e}"), "Pick a reader from list_readers.")
    })?;
    let card = ctx
        .connect(c_reader.as_c_str(), ShareMode::Shared, Protocols::ANY)
        .map_err(|e| match e {
            pcsc::Error::NoSmartcard => DeviceError::new(
                "NoCard",
                format!("No card present in reader '{reader}'"),
                "Plug in the Pico HSM / exit BOOTSEL mass-storage mode.",
            ),
            other => DeviceError::new(
                "ConnectFailed",
                format!("Failed to connect to '{reader}': {other}"),
                "Close OpenSC tools holding the card, then retry.",
            ),
        })?;
    f(&card)
}

#[tauri::command]
pub fn list_readers() -> Result<Vec<String>, DeviceError> {
    let _guard = lock_pcsc();
    let ctx = establish()?;
    let readers = ctx.list_readers_owned().map_err(|e| {
        DeviceError::new(
            "ListReadersFailed",
            format!("Failed to list readers: {e}"),
            pcsc_unavailable_hint(),
        )
    })?;
    Ok(readers.iter().map(|r| r.to_string_lossy().into_owned()).collect())
}

#[tauri::command]
pub fn card_status(reader: String) -> Result<CardStatus, DeviceError> {
    let _guard = lock_pcsc();
    let ctx = establish()?;
    let c_reader = CString::new(reader.clone()).map_err(|e| {
        DeviceError::new("BadReader", format!("Invalid reader name: {e}"), "Pick a reader from list_readers.")
    })?;
    let card = ctx
        .connect(c_reader.as_c_str(), ShareMode::Shared, Protocols::ANY)
        .map_err(|e| match e {
            pcsc::Error::NoSmartcard => DeviceError::new(
                "NoCard",
                format!("No card present in reader '{reader}'"),
                "Plug in the Pico HSM / exit BOOTSEL mass-storage mode.",
            ),
            other => DeviceError::new(
                "ConnectFailed",
                format!("Failed to connect to '{reader}': {other}"),
                "Close OpenSC tools holding the card, then retry.",
            ),
        })?;
    let status = card.status2_owned().map_err(|e| {
        DeviceError::new(
            "StatusFailed",
            format!("Failed to read card status: {e}"),
            "Reconnect the device and retry.",
        )
    })?;
    let atr = status.atr();
    let protocol = match status.protocol() {
        p if p == Protocol::T1 => "T=1",
        p if p == Protocol::T0 => "T=0",
        _ => "other",
    };
    Ok(CardStatus {
        reader,
        atr_hex: hex::encode(atr).to_uppercase(),
        protocol: protocol.to_string(),
    })
}

/// Raw APDU with applet pre-selection in the SAME connection.
/// Applet selection is card-global: without this, a raw transmit hits
/// whichever applet the last command selected (HSM vs rescue race -> 6E00).
/// `applet`: "hsm" (default) or "rescue".
#[tauri::command]
pub fn transmit_hsm(reader: String, apdu_hex: String, applet: Option<String>) -> Result<TransmitResult, DeviceError> {
    transmit_parsed(&reader, &apdu_hex, applet.as_deref())
}

fn transmit_parsed(reader: &str, apdu_hex: &str, applet: Option<&str>) -> Result<TransmitResult, DeviceError> {
    let apdu = hex::decode(apdu_hex.trim().replace([' ', ':'], "")).map_err(|e| {
        DeviceError::new(
            "BadApdu",
            format!("Invalid APDU hex: {e}"),
            "Provide even-length hex, e.g. '80640A0008'.",
        )
    })?;
    if apdu.is_empty() || apdu.len() > 64 * 1024 + 5 {
        return Err(DeviceError::new(
            "BadApdu",
            format!("APDU length {} out of range", apdu.len()),
            "Provide a valid short or extended APDU (max ~64 KiB).",
        ));
    }
    with_card(reader, |card| {
        match applet.unwrap_or("hsm") {
            "rescue" => {
                select_rescue(card)?;
            }
            "hsm" => {
                select_hsm(card)?;
            }
            other => {
                return Err(DeviceError::new(
                    "BadApplet",
                    format!("Unknown applet '{other}'"),
                    "Use 'hsm' or 'rescue'.",
                ))
            }
        }
        transmit_on_card(card, &apdu)
    })
}

#[tauri::command]
pub fn get_rtc_time(reader: String) -> Result<RtcTime, DeviceError> {
    // NOTE: `pin` was removed — the rescue clock needs no authentication.
    // (The HSM-applet DATETIME command no longer exists in this fork:
    // 6982 before login, 6A86 after.)
    with_card(&reader, |card| {
        select_rescue(card)?;
        let rsp = transmit_on_card(card, &[0x80, 0x1E, 0x04, 0x01, 0x00])?;
        if rsp.sw_hex == "6985" {
            return Err(DeviceError::new(
                "RtcNotSet",
                "Device clock was never set (SW=6985)".to_string(),
                "Use 'Sync with host time' once — afterwards the clock reads live.",
            ));
        }
        if rsp.sw_hex != "9000" {
            return Err(DeviceError::new(
                "RtcFailed",
                format!("RTC read failed with SW={}", rsp.sw_hex),
                "Retry; device may be busy (keygen can block for minutes).",
            ));
        }
        let raw = hex::decode(&rsp.data_hex).unwrap_or_default();
        if raw.len() != 8 {
            return Err(DeviceError::new(
                "RtcParse",
                format!("Expected 8-byte RTC response, got {} byte(s)", raw.len()),
                "Check firmware version (>= 5.x expected).",
            ));
        }
        let year = u16::from_be_bytes([raw[0], raw[1]]);
        let (month, day, hour, minute, second) = (raw[2], raw[3], raw[5], raw[6], raw[7]);
        Ok(RtcTime {
            display: format!("{year:04}-{month:02}-{day:02} {hour:02}:{minute:02}:{second:02}"),
            year,
            month,
            day,
            hour,
            minute,
            second,
        })
    })
}

#[derive(Debug, serde::Deserialize)]
pub struct HostDatetime {
    pub year: u16,
    pub month: u8,
    pub day: u8,
    /// 0 = Sunday .. 6 = Saturday (matches firmware doc).
    pub weekday: u8,
    pub hour: u8,
    pub minute: u8,
    pub second: u8,
}

/// Set the device RTC from host time (rescue `80 1C 02 01` + 8-byte datetime).
/// No PIN needed (no auth gate in the rescue handler).
#[tauri::command]
pub fn set_rtc_time(reader: String, dt: HostDatetime) -> Result<String, DeviceError> {
    if !(2000..=2099).contains(&dt.year)
        || !(1..=12).contains(&dt.month)
        || !(1..=31).contains(&dt.day)
        || dt.weekday > 6
        || dt.hour > 23
        || dt.minute > 59
        || dt.second > 59
    {
        return Err(DeviceError::new(
            "BadDatetime",
            "Host datetime out of range".to_string(),
            "The frontend must pass a valid local datetime.",
        ));
    }
    with_card(&reader, |card| {
        select_rescue(card)?;
        let mut apdu = vec![0x80, 0x1C, 0x02, 0x01];
        apdu.push(8);
        apdu.extend_from_slice(&dt.year.to_be_bytes());
        apdu.extend_from_slice(&[dt.month, dt.day, dt.weekday, dt.hour, dt.minute, dt.second]);
        apdu.push(0x00);
        let rsp = transmit_on_card(card, &apdu)?;
        if rsp.sw_hex != "9000" {
            return Err(DeviceError::new(
                "RtcSetFailed",
                format!("RTC set failed with SW={}", rsp.sw_hex),
                "Retry; device may be busy (keygen can block for minutes).",
            ));
        }
        Ok(format!(
            "Device clock set to {:04}-{:02}-{:02} {:02}:{:02}:{:02}.",
            dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second
        ))
    })
}

/// Decode a 16-hex-char SO-PIN to its 8 binary bytes (like OpenSC's
/// sc_hsm_encode_sopin). The device stores and compares the binary form —
/// sending the ASCII hex chars always fails. Never echoes the value.
fn decode_sopin_hex(sopin: &str) -> Result<[u8; 8], DeviceError> {
    if sopin.len() != 16 || !sopin.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err(DeviceError::new(
            "BadPin",
            "SO-PIN must be exactly 16 hexadecimal characters".to_string(),
            "Example format: 3537363231383830.",
        ));
    }
    let mut out = [0u8; 8];
    for (i, chunk) in sopin.as_bytes().chunks(2).enumerate() {
        out[i] = u8::from_str_radix(std::str::from_utf8(chunk).unwrap_or(""), 16).unwrap_or(0);
    }
    Ok(out)
}

/// Map a PIN-checking response (VERIFY / CHANGE / UNBLOCK) to Ok/Err.
/// `who` names the checked PIN ("User-PIN"/"SO-PIN") — never the value.
fn map_pin_result(rsp: &TransmitResult, who: &str) -> Result<(), DeviceError> {
    if rsp.sw_hex == "9000" {
        return Ok(());
    }
    if rsp.sw1 == 0x63 && (rsp.sw2 & 0xF0) == 0xC0 {
        let left = rsp.sw2 & 0x0F;
        if left == 0 {
            return Err(DeviceError::new(
                "PinBlocked",
                format!("Wrong PIN — {who} is now blocked"),
                "Unblock it with the SO-PIN (open Device Config > PIN management).",
            ));
        }
        return Err(DeviceError::new(
            "WrongPin",
            format!("Wrong {who} — {left} tries left"),
            "Check CAPS lock and try again.",
        ));
    }
    if rsp.sw_hex == "6983" || rsp.sw_hex == "6984" {
        return Err(DeviceError::new(
            "PinBlocked",
            format!("{who} is blocked"),
            "Unblock it with the SO-PIN (open Device Config > PIN management).",
        ));
    }
    if rsp.sw_hex == "6A88" {
        return Err(DeviceError::new(
            "NotInitialized",
            "Device not initialized (no PIN reference found)".to_string(),
            "Initialize the device first (open Device Config > Initialization).",
        ));
    }
    if rsp.sw_hex == "6986" {
        return Err(DeviceError::new(
            "NotAllowed",
            "Command not allowed by device configuration".to_string(),
            "The required option bit (e.g. RESET RETRY COUNTER) is not enabled.",
        ));
    }
    Err(DeviceError::new(
        "PinOpFailed",
        format!("Operation failed with SW={}", rsp.sw_hex),
        "Retry; if persistent, capture the trace for a bug report.",
    ))
}

/// Change User-PIN (0x81, ASCII) or SO-PIN (0x88, 16 hex chars -> 8 bytes):
/// `00 24 00 <ref>` + old + new. No prior login needed — the old PIN is
/// verified inside the command.
#[tauri::command]
pub fn change_pin(reader: String, pin_ref: u8, old_pin: String, new_pin: String) -> Result<String, DeviceError> {
    if pin_ref != 0x81 && pin_ref != 0x88 {
        return Err(DeviceError::new(
            "BadPinRef",
            "PIN reference must be 0x81 (user) or 0x88 (SO)".to_string(),
            "This is a frontend bug — please report.",
        ));
    }
    let who = if pin_ref == 0x81 { "User-PIN" } else { "SO-PIN" };
    // SO-PIN travels hex-decoded (8 bytes), User-PIN as ASCII bytes.
    let (old_bytes, new_bytes): (Vec<u8>, Vec<u8>) = if pin_ref == 0x88 {
        (decode_sopin_hex(&old_pin)?.to_vec(), decode_sopin_hex(&new_pin)?.to_vec())
    } else {
        for (label, p) in [("old", &old_pin), ("new", &new_pin)] {
            if p.is_empty() || p.len() > 32 || !p.bytes().all(|b| (0x20..0x7F).contains(&b)) {
                return Err(DeviceError::new(
                    "BadPin",
                    format!("{label} PIN must be 1-32 printable ASCII characters"),
                    "Check the documented PIN rules in the UI.",
                ));
            }
        }
        if new_pin.len() > 16 {
            return Err(DeviceError::new(
                "BadPin",
                "New PIN must be at most 16 characters".to_string(),
                "The firmware accepts 1-16 bytes for the new PIN.",
            ));
        }
        (old_pin.as_bytes().to_vec(), new_pin.as_bytes().to_vec())
    };
    with_card(&reader, |card| {
        select_hsm(card)?;
        let mut apdu = vec![0x00, 0x24, 0x00, pin_ref, (old_bytes.len() + new_bytes.len()) as u8];
        apdu.extend_from_slice(&old_bytes);
        apdu.extend_from_slice(&new_bytes);
        apdu.push(0x00);
        let rsp = transmit_on_card(card, &apdu)?;
        map_pin_result(&rsp, who)?;
        Ok(format!("{who} changed successfully."))
    })
}

/// Unblock the User-PIN: `00 2C 00 81` + SO-PIN (8 hex-decoded bytes) +
/// new User-PIN (ASCII). Requires the RESET RETRY COUNTER option bit.
#[tauri::command]
pub fn unblock_pin(reader: String, sopin: String, new_pin: String) -> Result<String, DeviceError> {
    let sopin_bin = decode_sopin_hex(&sopin)?;
    if new_pin.is_empty() || new_pin.len() > 16 || !new_pin.bytes().all(|b| (0x20..0x7F).contains(&b)) {
        return Err(DeviceError::new(
            "BadPin",
            "New User-PIN must be 1-16 printable ASCII characters".to_string(),
            "The User-PIN is 6-16 characters (check your provisioning).",
        ));
    }
    with_card(&reader, |card| {
        select_hsm(card)?;
        let mut apdu = vec![0x00, 0x2C, 0x00, 0x81, (sopin_bin.len() + new_pin.len()) as u8];
        apdu.extend_from_slice(&sopin_bin);
        apdu.extend_from_slice(new_pin.as_bytes());
        apdu.push(0x00);
        let rsp = transmit_on_card(card, &apdu)?;
        // NOTE: 63CX here counts against the SO-PIN counter, not the user's.
        if rsp.sw1 == 0x63 && (rsp.sw2 & 0xF0) == 0xC0 {
            let left = rsp.sw2 & 0x0F;
            if left == 0 {
                return Err(DeviceError::new(
                    "PinBlocked",
                    "Wrong SO-PIN — SO-PIN is now blocked".to_string(),
                    "The device must be re-initialized (loses all keys).",
                ));
            }
            return Err(DeviceError::new(
                "WrongPin",
                format!("Wrong SO-PIN — {left} SO tries left"),
                "Check CAPS lock and try again. This counter belongs to the SO-PIN.",
            ));
        }
        map_pin_result(&rsp, "User-PIN")?;
        Ok("User-PIN unblocked and set to the new PIN.".to_string())
    })
}

/// DKEK setup for initialization: None (omit tag 0x92), one random DKEK
/// (tag value 0x00), or N empty slots (tag value N, capped for sanity —
/// exact firmware max is a hardware-verification point).
/// NOTE: Random is protocol-complete but UI-hidden — a device-generated DKEK
/// is never shown to anyone, so no shares exist to distribute and cross-device
/// restore is impossible. Only N-slots (external shares) serve backup.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DkekSetup {
    None,
    Random,
    Slots(u8),
}

/// Build the INITIALIZE payload TLV: 0x81 user PIN, 0x82 SO-PIN (8 bytes from
/// 16 hex chars, same encoding as change/unblock), 0x91 retry limit,
/// 0x92 DKEK setup. Pure helper so encoding is unit-testable.
fn build_init_tlv(user_pin: &str, so_pin: &str, retries: u8, dkek: DkekSetup) -> Result<Vec<u8>, DeviceError> {
    if user_pin.len() < 6 || user_pin.len() > 16 || !user_pin.bytes().all(|b| (0x20..0x7F).contains(&b)) {
        return Err(DeviceError::new(
            "BadPin",
            "User-PIN must be 6-16 printable ASCII characters".to_string(),
            "Pick a new User-PIN first.",
        ));
    }
    // SO-PIN travels hex-decoded (8 bytes), like the change/unblock flows
    // (sc_hsm_encode_sopin ecosystem standard) — a raw-ASCII SO-PIN set here
    // would be unreachable through change/unblock afterwards.
    let sopin_bin = decode_sopin_hex(so_pin).map_err(|_| {
        DeviceError::new(
            "BadPin",
            "SO-PIN must be exactly 16 hexadecimal characters".to_string(),
            "Same format as PIN change/unblock (encodes to 8 bytes).",
        )
    })?;
    if retries < 1 || retries > 15 {
        return Err(DeviceError::new(
            "BadRetries",
            "Retry limit must be 1-15".to_string(),
            "3 is the recommended default.",
        ));
    }
    if let DkekSetup::Slots(n) = dkek {
        if n < 1 || n > 8 {
            return Err(DeviceError::new(
                "BadDkek",
                "DKEK slots must be 1-8 (or none/random)".to_string(),
                "The exact firmware maximum is a hardware-verification point.",
            ));
        }
    }
    let mut tlv = vec![0x81, user_pin.len() as u8];
    tlv.extend_from_slice(user_pin.as_bytes());
    tlv.push(0x82);
    tlv.push(sopin_bin.len() as u8);
    tlv.extend_from_slice(&sopin_bin);
    tlv.extend_from_slice(&[0x91, 0x01, retries]);
    match dkek {
        DkekSetup::None => {}
        DkekSetup::Random => tlv.extend_from_slice(&[0x92, 0x01, 0x00]),
        DkekSetup::Slots(n) => tlv.extend_from_slice(&[0x92, 0x01, n]),
    }
    Ok(tlv)
}

/// Initialize (or re-initialize) the device: `00 50 00 00` + init TLV.
/// WIPES ALL KEYS (`file_initialize_flash`). Tags: 0x81 user PIN,
/// 0x82 SO-PIN (16 hex chars, same wire encoding as change/unblock),
/// 0x91 retry limit, 0x92 DKEK setup. No 0x80 (firmware defaults apply). No auth needed;
/// after success the device is authenticated — the frontend should adopt
/// the new User-PIN as session PIN without an extra VERIFY.
#[tauri::command]
pub fn init_device(
    reader: String,
    user_pin: String,
    so_pin: String,
    retries: u8,
    dkek_slots: Option<u8>,
    dkek_random: bool,
) -> Result<String, DeviceError> {
    let dkek = match (dkek_slots, dkek_random) {
        (Some(n), _) => DkekSetup::Slots(n),
        (None, true) => DkekSetup::Random,
        (None, false) => DkekSetup::None,
    };
    let tlv = build_init_tlv(&user_pin, &so_pin, retries, dkek)?;
    with_card(&reader, |card| {
        select_hsm(card)?;
        let apdu = case4(0x00, 0x50, 0x00, 0x00, &tlv).ok_or_else(|| {
            DeviceError::new(
                "InitFailed",
                "Could not encode initialize APDU".to_string(),
                "This is a frontend bug — please report.",
            )
        })?;
        let rsp = transmit_on_card(card, &apdu)?;
        if rsp.sw_hex == "9000" {
            return Ok("Device initialized. All previous keys are erased.".to_string());
        }
        if rsp.sw_hex == "6982" {
            return Err(DeviceError::new(
                "InitRefused",
                "Initialize refused (SW=6982 — secure lock may be set)".to_string(),
                "Secure-locked devices need the MKEK mask path, which this app does not do yet.",
            ));
        }
        Err(DeviceError::new(
            "InitFailed",
            format!("Initialize failed with SW={}", rsp.sw_hex),
            "Nothing was changed unless the wipe already ran — check the device state.",
        ))
    })
}

/// Verify the User-PIN on an already-selected card.
/// Shared by `login` and authed commands (VERIFY + op on one connection).
/// The PIN itself never appears in any message, log, or response.
fn verify_pin(card: &pcsc::Card, pin: &str) -> Result<(), DeviceError> {
    if pin.is_empty() || pin.len() > 32 || !pin.bytes().all(|b| (0x20..0x7F).contains(&b)) {
        return Err(DeviceError::new(
            "BadPin",
            "PIN must be 1-32 printable ASCII characters".to_string(),
            "The User-PIN is 6-16 characters (check your provisioning).",
        ));
    }
    let mut apdu = vec![0x00, 0x20, 0x00, 0x81, pin.len() as u8];
    apdu.extend_from_slice(pin.as_bytes());
    apdu.push(0x00);
    let rsp = transmit_raw(card, &apdu)?;
    if rsp.sw_hex == "9000" {
        return Ok(());
    }
    if rsp.sw1 == 0x63 && (rsp.sw2 & 0xF0) == 0xC0 {
        let left = rsp.sw2 & 0x0F;
        if left == 0 {
            return Err(DeviceError::new(
                "PinBlocked",
                "Wrong PIN — User-PIN is now blocked".to_string(),
                "Unblock it with the SO-PIN (open Device Config > PIN management).",
            ));
        }
        return Err(DeviceError::new(
            "WrongPin",
            format!("Wrong PIN — {left} tries left"),
            "Check CAPS lock and try again.",
        ));
    }
    if rsp.sw_hex == "6983" || rsp.sw_hex == "6984" {
        return Err(DeviceError::new(
            "PinBlocked",
            "User-PIN is blocked".to_string(),
            "Unblock it with the SO-PIN (open Device Config > PIN management).",
        ));
    }
    Err(DeviceError::new(
        "LoginFailed",
        format!("Login failed with SW={}", rsp.sw_hex),
        "Retry; if persistent, capture the trace for a bug report.",
    ))
}

/// Verify the User-PIN (`00 20 00 81`, no prior auth needed).
/// A convenience check for the PIN dialog; authed commands re-verify
/// on their own connection anyway. Returns retries-left on failure.
/// The PIN itself never appears in any message, log, or response.
#[tauri::command]
pub fn login(reader: String, pin: String) -> Result<String, DeviceError> {
    with_card(&reader, |card| {
        select_hsm(card)?;
        verify_pin(card, &pin)?;
        Ok("PIN correct.".to_string())
    })
}

/// DKEK key-domain status: total shares, remaining imports, KCV.
/// From `00 52` (KEY DOMAIN): response is [total, remaining, KCV×8, …XKEK?].
/// total == 0 means no DKEK configured for the domain. KCV (first 8 bytes of
/// SHA-256 over the DKEK) identifies the key without exposing it — compare
/// across boards/people to prove the same DKEK.
#[derive(Debug, Serialize, Clone)]
pub struct DkekStatus {
    pub domain: u8,
    /// Configured share count (0 = no DKEK).
    pub total: u8,
    /// Shares still missing (0 = complete).
    pub remaining: u8,
    /// KCV as 16 uppercase hex chars (zeros when no DKEK material yet).
    pub kcv_hex: String,
    /// True when XKEK bytes trail the KCV (XKEK domain).
    pub has_xkek: bool,
}

/// Parse a KEY DOMAIN status response (≥10 bytes).
fn parse_dkek_status(domain: u8, data_hex: &str) -> Result<DkekStatus, DeviceError> {
    let raw = hex::decode(data_hex).map_err(|_| {
        DeviceError::new(
            "DkekParse",
            "Undecodable key-domain response".to_string(),
            "Capture the trace for a bug report.",
        )
    })?;
    if raw.len() < 10 {
        return Err(DeviceError::new(
            "DkekParse",
            format!("Key-domain response too short ({} bytes)", raw.len()),
            "Capture the trace for a bug report.",
        ));
    }
    Ok(DkekStatus {
        domain,
        total: raw[0],
        remaining: raw[1],
        kcv_hex: hex::encode(&raw[2..10]).to_uppercase(),
        has_xkek: raw.len() > 10,
    })
}

fn map_key_domain_sw(rsp: &TransmitResult, what: &str) -> Result<(), DeviceError> {
    if rsp.sw_hex == "9000" {
        return Ok(());
    }
    if rsp.sw_hex == "6982" {
        return Err(DeviceError::auth_required(
            "DkekAuth",
            format!("{what} needs User-PIN authentication (SW=6982)"),
        ));
    }
    if rsp.sw_hex == "6986" {
        return Err(DeviceError::new(
            "DkekComplete",
            format!("{what}: domain already complete (SW=6986)"),
            "No more shares can be imported here.",
        ));
    }
    if rsp.sw_hex == "6A88" {
        return Err(DeviceError::new(
            "NoDkekDomain",
            format!("{what}: no key domain here (SW=6A88)"),
            "Set up a DKEK domain at initialization first.",
        ));
    }
    Err(DeviceError::new(
        "DkekFailed",
        format!("{what} failed with SW={}", rsp.sw_hex),
        "Retry; if persistent, capture the trace for a bug report.",
    ))
}

/// Read a DKEK key-domain status (`00 52 00 <domain>`, no data, no auth).
#[tauri::command]
pub fn dkek_status(reader: String, domain: u8) -> Result<DkekStatus, DeviceError> {
    if domain >= 16 {
        return Err(DeviceError::new(
            "BadDomain",
            "Key domain must be 0-15".to_string(),
            "This is a frontend bug — please report.",
        ));
    }
    with_card(&reader, |card| {
        select_hsm(card)?;
        let rsp = transmit_on_card(card, &[0x00, 0x52, 0x00, domain, 0x00])?;
        if rsp.sw_hex == "6B00" || rsp.sw_hex == "6A86" {
            return Err(DeviceError::new(
                "NoDkekDomain",
                format!("No DKEK domain {domain} configured (SW={})", rsp.sw_hex),
                "Create the domain below, or initialize the device with DKEK support.",
            ));
        }
        map_key_domain_sw(&rsp, "DKEK status")?;
        parse_dkek_status(domain, &rsp.data_hex)
    })
}

/// Import one DKEK share (`00 52 00 <domain>` + 32 bytes).
/// XOR N-of-N: every share XORs into the slot, order irrelevant; the last
/// share finalizes (MKEK-wrap), which is why the session PIN is verified
/// first when provided. Response carries the new status (progress + KCV).
#[tauri::command]
pub fn dkek_import_share(
    reader: String,
    domain: u8,
    share_hex: String,
    pin: Option<String>,
) -> Result<DkekStatus, DeviceError> {
    if domain >= 16 {
        return Err(DeviceError::new(
            "BadDomain",
            "Key domain must be 0-15".to_string(),
            "This is a frontend bug — please report.",
        ));
    }
    let share = hex::decode(share_hex.trim()).map_err(|_| {
        DeviceError::new(
            "BadShare",
            "Share must be 64 hex characters (32 bytes)".to_string(),
            "Paste one share exactly as generated.",
        )
    })?;
    if share.len() != 32 {
        return Err(DeviceError::new(
            "BadShare",
            format!("Share is {} bytes (need 32)", share.len()),
            "Paste one share exactly as generated.",
        ));
    }
    with_card(&reader, |card| {
        select_hsm(card)?;
        if let Some(ref p) = pin {
            verify_pin(card, p)?;
        }
        let mut apdu = vec![0x00, 0x52, 0x00, domain, 0x20];
        apdu.extend_from_slice(&share);
        apdu.push(0x00);
        let rsp = transmit_on_card(card, &apdu)?;
        map_key_domain_sw(&rsp, "DKEK share import")?;
        parse_dkek_status(domain, &rsp.data_hex)
    })
}

/// Create a DKEK key domain (`00 52 01 <domain>` + 1 count byte, auth required).
/// Fresh domains start empty (0 of N imported). Only possible on a never-used
/// domain slot; existing domains answer 6B00. Returns the fresh status.
#[tauri::command]
pub fn dkek_setup_domain(
    reader: String,
    domain: u8,
    shares: u8,
    pin: Option<String>,
) -> Result<DkekStatus, DeviceError> {
    if domain >= 16 {
        return Err(DeviceError::new(
            "BadDomain",
            "Key domain must be 0-15".to_string(),
            "This is a frontend bug — please report.",
        ));
    }
    if shares < 1 || shares > 8 {
        return Err(DeviceError::new(
            "BadDkek",
            "Share count must be 1-8".to_string(),
            "Pick how many shares this domain needs (all of them, XOR N-of-N).",
        ));
    }
    with_card(&reader, |card| {
        select_hsm(card)?;
        if let Some(ref p) = pin {
            verify_pin(card, p)?;
        }
        let rsp = transmit_on_card(card, &[0x00, 0x52, 0x01, domain, 0x01, shares, 0x00])?;
        if rsp.sw_hex == "6982" {
            return Err(DeviceError::auth_required(
                "DkekAuth",
                "Domain setup needs User-PIN authentication (SW=6982)".to_string(),
            ));
        }
        if rsp.sw_hex == "6985" {
            return Err(DeviceError::auth_required(
                "DkekAuth",
                "Domain setup needs a logged-in session (SW=6985)".to_string(),
            ));
        }
        if rsp.sw_hex == "6B00" {
            return Err(DeviceError::new(
                "DomainExists",
                format!("Domain {domain} is already set up (SW=6B00)"),
                "Domains cannot be redefined — pick a fresh domain id.",
            ));
        }
        map_key_domain_sw(&rsp, "DKEK domain setup")?;
        parse_dkek_status(domain, &rsp.data_hex)
    })
}

fn map_wrap_sw(rsp: &TransmitResult, what: &str) -> Result<(), DeviceError> {
    if rsp.sw_hex == "9000" {
        return Ok(());
    }
    if rsp.sw_hex == "6982" {
        return Err(DeviceError::auth_required(
            "WrapAuth",
            format!("{what} needs User-PIN authentication (SW=6982)"),
        ));
    }
    if rsp.sw_hex == "6985" {
        return Err(DeviceError::new(
            "WrapNotAllowed",
            format!("{what}: key forbids it (SW=6985)"),
            "Wrapping needs the WRAP purpose; AES wrapping additionally needs a button press. Check the key details.",
        ));
    }
    if rsp.sw_hex == "6A88" {
        return Err(DeviceError::new(
            "NoDkekDomain",
            format!("{what}: key has no complete DKEK domain (SW=6A88)"),
            "Import all DKEK shares first.",
        ));
    }
    if rsp.sw_hex == "6A82" {
        return Err(DeviceError::new(
            "KeyMissing",
            format!("{what}: key not found (SW=6A82)"),
            "Reload the key list.",
        ));
    }
    Err(DeviceError::new(
        "WrapFailed",
        format!("{what} failed with SW={}", rsp.sw_hex),
        "Retry; if persistent, capture the trace for a bug report.",
    ))
}

/// Wrap a key with its DKEK domain (`00 72 <id> 92`, auth required).
/// Returns the wrapped blob (hex) — self-describing, safe to store
/// off-device. The key needs the WRAP purpose and a complete DKEK domain;
/// AES wrapping additionally needs a button press on the device.
#[tauri::command]
pub fn wrap_key(reader: String, id: u8, pin: Option<String>) -> Result<String, DeviceError> {
    if id == 0 {
        return Err(DeviceError::new(
            "RefusedWrap",
            "The device key (ID 0) is not backed up".to_string(),
            "It is recreated at initialization.",
        ));
    }
    with_card(&reader, |card| {
        select_hsm(card)?;
        if let Some(ref p) = pin {
            verify_pin(card, p)?;
        }
        let rsp = transmit_on_card(card, &[0x00, 0x72, id, 0x92, 0x00])?;
        map_wrap_sw(&rsp, &format!("Wrap of key {id}"))?;
        let blob = hex::decode(&rsp.data_hex).unwrap_or_default();
        if blob.is_empty() {
            return Err(DeviceError::new(
                "WrapFailed",
                format!("Wrap of key {id} returned no data"),
                "Retry; if persistent, capture the trace for a bug report.",
            ));
        }
        Ok(hex::encode(&blob).to_uppercase())
    })
}

/// Unwrap a wrapped blob as a key id (`00 74 <id> 93` + blob, auth required).
/// The device tries all domains for a matching DKEK; a blob encrypted for
/// an unknown DKEK fails honestly (no partial state).
#[tauri::command]
pub fn unwrap_key(reader: String, id: u8, blob_hex: String, pin: Option<String>) -> Result<String, DeviceError> {
    if id == 0 {
        return Err(DeviceError::new(
            "RefusedUnwrap",
            "Cannot restore onto the device key (ID 0)".to_string(),
            "Pick a user key id.",
        ));
    }
    let blob = hex::decode(blob_hex.trim()).map_err(|_| {
        DeviceError::new(
            "BadBlob",
            "Wrapped blob is not valid hex".to_string(),
            "This backup file is corrupt — use another copy.",
        )
    })?;
    if blob.is_empty() || blob.len() > 4096 {
        return Err(DeviceError::new(
            "BadBlob",
            format!("Wrapped blob size {} out of range", blob.len()),
            "This backup file is corrupt — use another copy.",
        ));
    }
    with_card(&reader, |card| {
        select_hsm(card)?;
        if let Some(ref p) = pin {
            verify_pin(card, p)?;
        }
        // Blobs (RSA-4096 is ~1 KiB wrapped) exceed short APDU: extended form via case4.
        let apdu = case4(0x00, 0x74, id, 0x93, &blob).ok_or_else(|| {
            DeviceError::new(
                "BadBlob",
                "Wrapped blob too large to send".to_string(),
                "This backup file is corrupt — use another copy.",
            )
        })?;
        let rsp = transmit_on_card(card, &apdu)?;
        if rsp.sw_hex == "6982" {
            return Err(DeviceError::auth_required(
                "WrapAuth",
                "Unwrap needs User-PIN authentication (SW=6982)".to_string(),
            ));
        }
        // Wrong DKEK on every domain (6400 exec / 6984 data-invalid — the
        // firmware never returns 6A80 here): either no local DKEK matches
        // or the blob itself is corrupt. No partial state either way.
        if rsp.sw_hex == "6400" || rsp.sw_hex == "6984" {
            return Err(DeviceError::new(
                "WrongDkek",
                format!("No local DKEK matches this backup, or the blob is corrupt (SW={})", rsp.sw_hex),
                "Import the DKEK shares this backup was made with first (KCV must match).",
            ));
        }
        map_wrap_sw(&rsp, &format!("Unwrap as key {id}"))?;
        Ok(format!("Key restored as ID {id}."))
    })
}

/// Firmware version + device options from the SELECT FCP (tag `85 05`).
#[tauri::command]
pub fn get_version(reader: String) -> Result<FirmwareVersion, DeviceError> {
    with_card(&reader, |card| {
        let fcp = select_hsm(card)?;
        let pos = fcp.windows(2).position(|w| w == [0x85, 0x05]).ok_or_else(|| {
            DeviceError::new(
                "VersionParse",
                "SELECT FCP has no tag 0x85 (expected on Pico HSM firmware)".to_string(),
                "Check the device runs Pico HSM firmware (>= 5.x).",
            )
        })?;
        let v = fcp.get(pos + 2..pos + 7).ok_or_else(|| {
            DeviceError::new(
                "VersionParse",
                "SELECT FCP tag 0x85 is truncated".to_string(),
                "Retry; if persistent, capture the trace for a bug report.",
            )
        })?;
        Ok(FirmwareVersion {
            display: format!("{}.{}", v[3], v[4]),
            major: v[3],
            minor: v[4],
            opts_hex: format!("{:02X}{:02X}", v[0], v[1]),
        })
    })
}

/// Remaining PIN retries without authenticating (`00 20 00 <ref>`).
/// Shared helper for User-PIN (0x81) and SO-PIN (0x88).
fn pin_retries(card: &pcsc::Card, pin_ref: u8) -> Result<PinStatus, DeviceError> {
    let apdu = [0x00, 0x20, 0x00, pin_ref];
    let rsp = transmit_raw(card, &apdu)?;
    if rsp.sw_hex == "9000" {
        return Ok(PinStatus { retries: -1, blocked: false, sw_hex: rsp.sw_hex });
    }
    if rsp.sw1 == 0x63 && (rsp.sw2 & 0xF0) == 0xC0 {
        let left = (rsp.sw2 & 0x0F) as i8;
        return Ok(PinStatus { retries: left, blocked: left == 0, sw_hex: rsp.sw_hex });
    }
    if rsp.sw_hex == "6983" || rsp.sw_hex == "6984" {
        return Ok(PinStatus { retries: -2, blocked: true, sw_hex: rsp.sw_hex });
    }
    Err(DeviceError::new(
        "PinStatusFailed",
        format!("Unexpected VERIFY response SW={}", rsp.sw_hex),
        "Retry; if persistent, capture the trace for a bug report.",
    ))
}

/// Remaining User-PIN retries without authenticating.
#[tauri::command]
pub fn get_pin_retries(reader: String) -> Result<PinStatus, DeviceError> {
    with_card(&reader, |card| {
        select_hsm(card)?;
        pin_retries(card, 0x81)
    })
}

/// Remaining SO-PIN retries without authenticating.
#[tauri::command]
pub fn get_sopin_retries(reader: String) -> Result<PinStatus, DeviceError> {
    with_card(&reader, |card| {
        select_hsm(card)?;
        pin_retries(card, 0x88)
    })
}

/// SELECT the rescue applet; returns raw response bytes on success.
fn select_rescue(card: &pcsc::Card) -> Result<Vec<u8>, DeviceError> {
    let rsp = transmit_on_card(card, RESCUE_SELECT_APDU)?;
    if rsp.sw_hex != "9000" {
        return Err(DeviceError::new(
            "RescueSelectFailed",
            format!("SELECT rescue applet failed with SW={}", rsp.sw_hex),
            "The firmware may not expose the rescue applet over CCID.",
        ));
    }
    hex::decode(&rsp.data_hex).map_err(|e| {
        DeviceError::new(
            "RescueParse",
            format!("Rescue SELECT response is not hex: {e}"),
            "Retry; if persistent, capture the trace for a bug report.",
        )
    })
}

/// MCU / product / version / board id from the rescue applet (no auth).
/// Falls back to an error when the rescue applet is not selectable.
#[tauri::command]
pub fn get_platform_info(reader: String) -> Result<PlatformInfo, DeviceError> {
    with_card(&reader, |card| {
        let raw = select_rescue(card)?;
        if raw.len() < 4 {
            return Err(DeviceError::new(
                "PlatformParse",
                format!("Expected >= 4-byte rescue response, got {} byte(s)", raw.len()),
                "Capture the trace for a bug report.",
            ));
        }
        let platform = match raw[0] {
            0 => "RP2040",
            1 => "RP2350",
            2 => "ESP32-S3",
            3 => "EMULATION",
            4 => "ESP32-S2",
            b => return Err(DeviceError::new(
                "PlatformParse",
                format!("Unknown MCU id {b}"),
                "Newer firmware with an unknown MCU id — please report.",
            )),
        };
        let product = match raw[1] {
            0 => "UNKNOWN",
            1 => "HSM",
            2 => "FIDO",
            3 => "OPENPGP",
            b => return Err(DeviceError::new(
                "PlatformParse",
                format!("Unknown product id {b}"),
                "Please report with firmware version.",
            )),
        };
        Ok(PlatformInfo {
            platform: platform.to_string(),
            product: product.to_string(),
            version: format!("{}.{}", raw[2], raw[3]),
            board_hex: hex::encode(&raw[4..]).to_uppercase(),
            raw_hex: hex::encode(&raw).to_uppercase(),
        })
    })
}

/// Flash usage from the rescue applet (`80 1E 02 00`, no auth).
/// 5x u32 BE free/used/total/files/flash_size, plus firmware size on Pico.
#[tauri::command]
pub fn get_flash_info(reader: String) -> Result<FlashInfo, DeviceError> {
    with_card(&reader, |card| {
        select_rescue(card)?;
        let rsp = transmit_on_card(card, &[0x80, 0x1E, 0x02, 0x00, 0x00])?;
        if rsp.sw_hex != "9000" {
            return Err(DeviceError::new(
                "FlashFailed",
                format!("FLASH INFO read failed with SW={}", rsp.sw_hex),
                "Check firmware version (>= 6.x expected for rescue READ).",
            ));
        }
        let raw = hex::decode(&rsp.data_hex).unwrap_or_default();
        if raw.len() < 20 {
            return Err(DeviceError::new(
                "FlashParse",
                format!("Expected >= 20-byte FLASH response, got {} byte(s)", raw.len()),
                "Capture the trace for a bug report.",
            ));
        }
        let u = |o: usize| u32::from_be_bytes([raw[o], raw[o + 1], raw[o + 2], raw[o + 3]]);
        Ok(FlashInfo {
            free_bytes: u(0),
            used_bytes: u(4),
            total_bytes: u(8),
            file_count: u(12),
            flash_bytes: u(16),
            firmware_bytes: if raw.len() >= 24 { Some(u(20)) } else { None },
            raw_hex: rsp.data_hex,
        })
    })
}

/// Secure-boot status from the rescue applet (`80 1E 03 00`, no auth).
#[tauri::command]
pub fn get_secure_info(reader: String) -> Result<SecureInfo, DeviceError> {
    with_card(&reader, |card| {
        select_rescue(card)?;
        let rsp = transmit_on_card(card, &[0x80, 0x1E, 0x03, 0x00, 0x00])?;
        if rsp.sw_hex != "9000" {
            return Err(DeviceError::new(
                "SecureFailed",
                format!("SECURE INFO read failed with SW={}", rsp.sw_hex),
                "Check firmware version (>= 6.x expected for rescue READ).",
            ));
        }
        let raw = hex::decode(&rsp.data_hex).unwrap_or_default();
        if raw.len() < 3 {
            return Err(DeviceError::new(
                "SecureParse",
                format!("Expected >= 3-byte SECURE response, got {} byte(s)", raw.len()),
                "Capture the trace for a bug report.",
            ));
        }
        Ok(SecureInfo {
            secure_boot: raw[0] != 0,
            locked: raw[1] != 0,
            boot_key: raw[2],
        })
    })
}

/// Write press-confirm (0x0100) and key-counter (0x0200) option bits.
/// Read-modify-write in one connection; SECURE_LOCK bit and low byte are
/// always preserved. Needs User-PIN authentication (same-connection VERIFY).
#[tauri::command]
pub fn set_dynops(
    reader: String,
    press_confirm: bool,
    key_counter: bool,
    pin: Option<String>,
) -> Result<String, DeviceError> {
    with_card(&reader, |card| {
        select_hsm(card)?;
        let read = transmit_on_card(card, &[0x80, 0x64, 0x06, 0x00, 0x00])?;
        if read.sw_hex != "9000" {
            return Err(DeviceError::new(
                "DynopsFailed",
                format!("DYNOPS read failed with SW={}", read.sw_hex),
                "Retry; device may be busy (keygen can block for minutes).",
            ));
        }
        let cur = hex::decode(&read.data_hex).unwrap_or_default();
        if cur.len() != 2 {
            return Err(DeviceError::new(
                "DynopsParse",
                format!("Expected 2-byte DYNOPS response, got {} byte(s)", cur.len()),
                "Capture the trace for a bug report.",
            ));
        }
        let mut high = cur[0];
        if press_confirm {
            high |= 0x01;
        } else {
            high &= !0x01;
        }
        if key_counter {
            high |= 0x02;
        } else {
            high &= !0x02;
        }
        if let Some(ref p) = pin {
            verify_pin(card, p)?;
        }
        let rsp = transmit_on_card(card, &[0x80, 0x64, 0x06, 0x00, 0x01, high, 0x00])?;
        if rsp.sw_hex == "6982" {
            return Err(DeviceError::auth_required(
                "DynopsAuth",
                "DYNOPS write needs User-PIN authentication (SW=6982)".to_string(),
            ));
        }
        if rsp.sw_hex != "9000" {
            return Err(DeviceError::new(
                "DynopsWriteFailed",
                format!("DYNOPS write failed with SW={}", rsp.sw_hex),
                "Retry; if persistent, capture the trace for a bug report.",
            ));
        }
        Ok("Dynamic options updated.".to_string())
    })
}

/// Reboot the device via the rescue applet (`80 1F <mode> 00`).
/// `bootsel=true` reboots into BOOTSEL mass-storage mode (Pico targets
/// only and requires user presence — confirm the button when asked).
/// The card disconnects on success; the caller must handle the absence.
/// NOTE: on success the card handle is intentionally leaked
/// (`mem::forget`) instead of disconnected — SCardDisconnect against an
/// already-vanished USB device crashed the process (0xC0000409).
#[tauri::command]
pub fn reboot_device(reader: String, bootsel: bool) -> Result<String, DeviceError> {
    let done_msg = if bootsel {
        "Rebooting into BOOTSEL mode — the device will disconnect."
    } else {
        "Rebooting — the device will disconnect briefly."
    };
    // Transport race: EV_RESET reboots asynchronously, so the 9000 response
    // can be lost mid-flight (device vanishes = reboot in progress, not a
    // failure). Confirm the outcome below instead of failing on the spot.
    let tx_err = {
        let _guard = lock_pcsc();
        let ctx = establish()?;
        let c_reader = CString::new(reader.clone()).map_err(|e| {
            DeviceError::new("BadReader", format!("Invalid reader name: {e}"), "Pick a reader from list_readers.")
        })?;
        let card = ctx
            .connect(c_reader.as_c_str(), ShareMode::Shared, Protocols::ANY)
            .map_err(|e| match e {
                pcsc::Error::NoSmartcard => DeviceError::new(
                    "NoCard",
                    format!("No card present in reader '{reader}'"),
                    "Plug in the Pico HSM / exit BOOTSEL mass-storage mode.",
                ),
                other => DeviceError::new(
                    "ConnectFailed",
                    format!("Failed to connect to '{reader}': {other}"),
                    "Close OpenSC tools holding the card, then retry.",
                ),
            })?;
        select_rescue(&card)?;
        let apdu = [0x80, 0x1F, if bootsel { 0x01 } else { 0x00 }, 0x00];
        match transmit_raw(&card, &apdu) {
            Ok(rsp) if rsp.sw_hex == "9000" => {
                // Device reboots NOW — never touch this handle again.
                std::mem::forget(card);
                return Ok(done_msg.to_string());
            }
            Ok(rsp) if rsp.sw_hex == "6D00" => {
                return Err(DeviceError::new(
                    "RebootUnsupported",
                    "Reboot command not supported by this firmware/target".to_string(),
                    "BOOTSEL reboot is a Pico-target feature; ESP32 uses the ROM bootloader instead.",
                ))
            }
            Ok(rsp) if rsp.sw_hex == "6985" => {
                return Err(DeviceError::new(
                    "PresenceRequired",
                    "Device requires user presence for reboot".to_string(),
                    "Press the BOOTSEL/confirm button on the device, then retry within a few seconds.",
                ))
            }
            Ok(rsp) => {
                return Err(DeviceError::new(
                    "RebootFailed",
                    format!("Reboot failed with SW={}", rsp.sw_hex),
                    "Retry; if persistent, capture the trace for a bug report.",
                ))
            }
            Err(e) => e,
        }
    };
    // Lock released: the reboot APDU died in transit. If the reset landed,
    // the board proves it within seconds — only then call it a success.
    if confirm_reboot_outcome(&reader, bootsel) {
        return Ok(done_msg.to_string());
    }
    Err(tx_err)
}

/// Verify a reboot whose response was lost: BOOTSEL drive appears, or the
/// board answers again in normal mode. ~20 s grace, then give up honestly.
fn confirm_reboot_outcome(reader: &str, bootsel: bool) -> bool {
    let deadline = Instant::now() + Duration::from_secs(20);
    loop {
        if bootsel {
            if scan_bootsel_drive().is_some() {
                return true;
            }
        } else if get_version(reader.to_string()).is_ok() {
            return true;
        }
        if Instant::now() > deadline {
            return false;
        }
        std::thread::sleep(Duration::from_millis(500));
    }
}

/// BOOTSEL mass-storage drive (lives here so `reboot_device` can confirm the
/// outcome without a module cycle with `firmware`).
#[derive(Debug, Serialize, Clone)]
pub(crate) struct BootselDrive {
    pub drive: String,
    pub model: String,
}

/// Parse the `Model:` line of an INFO_UF2.TXT body.
pub(crate) fn parse_info_uf2(body: &str) -> Option<String> {
    body.lines().find_map(|l| {
        let t = l.trim();
        t.strip_prefix("Model:").map(|m| m.trim().to_string())
    })
}

/// Scan A..Z for a drive carrying INFO_UF2.TXT (Windows-first).
/// Missing/unreadable letters are skipped (a `?` here would abort the whole
/// scan at the first absent drive — real bug, 01.10.2026).
pub(crate) fn scan_bootsel_drive() -> Option<BootselDrive> {
    for letter in b'A'..=b'Z' {
        let info_path = format!("{}:\\INFO_UF2.TXT", letter as char);
        let body = match std::fs::read_to_string(&info_path) {
            Ok(b) => b,
            Err(_) => continue,
        };
        // Sanity: a real UF2 bootloader marker, not a stray same-named file.
        if !body.contains("UF2") {
            continue;
        }
        let model = parse_info_uf2(&body).unwrap_or_else(|| "unknown".to_string());
        return Some(BootselDrive {
            drive: format!("{}:", letter as char),
            model,
        });
    }
    None
}
#[tauri::command]
pub fn get_serial(reader: String) -> Result<SerialInfo, DeviceError> {
    with_card(&reader, |card| {
        select_hsm(card)?;
        // EF 2F02 exceeds one response (observed: ~442-byte CVC, card
        // returns 256-byte chunks). Re-read with incremented offset until a
        // short chunk arrives or SW is 6282-exhausted (same loop OpenSC's
        // sc-hsm driver uses). A full 256-byte chunk with SW 9000 may hide
        // more data, so only a short chunk terminates on 9000.
        let mut raw: Vec<u8> = Vec::new();
        let mut offset: usize = 0;
        loop {
            let off = (offset & 0xFFFF) as u16;
            let apdu = vec![
                0x00, 0xB1, 0x2F, 0x02, 0x04, 0x54, 0x02,
                (off >> 8) as u8, (off & 0xFF) as u8, 0x00,
            ];
            let rsp = transmit_raw(card, &apdu)?;
            if rsp.sw_hex != "9000" && rsp.sw_hex != "6282" {
                return Err(DeviceError::new(
                    "SerialFailed",
                    format!("EF 2F02 read failed with SW={}", rsp.sw_hex),
                    "The device may not be initialized (no device certificate yet).",
                ));
            }
            let chunk = hex::decode(&rsp.data_hex).unwrap_or_default();
            if chunk.is_empty() {
                break;
            }
            raw.extend_from_slice(&chunk);
            offset += chunk.len();
            if raw.len() > 8192 {
                return Err(DeviceError::new(
                    "SerialFailed",
                    "EF 2F02 exceeds 8 KiB without terminating".to_string(),
                    "Capture the trace for a bug report.",
                ));
            }
            if rsp.sw_hex == "9000" && chunk.len() < 256 {
                break;
            }
        }
        // Device id: prefer CHR (tag `5F 20`); observed firmware issues
        // certs with the id in CAR (single-byte tag `0x42`) instead.
        let val_off = if let Some(pos) = raw.windows(2).position(|w| w == [0x5F, 0x20]) {
            pos + 2
        } else if let Some(pos) = raw.iter().position(|&b| b == 0x42) {
            pos + 1
        } else {
            return Err(DeviceError::new(
                "SerialParse",
                format!("No CHR (5F 20) or CAR (42) tag found in {}-byte EF 2F02", raw.len()),
                "Post the raw dump (`opensc-tool -s 00B12F02045402000000`) for analysis.",
            ));
        };
        // BER-TLV length: short form (< 0x80) or long form 0x81 LL / 0x82 LLLL.
        let mut len_at = val_off;
        let first = *raw.get(len_at).ok_or_else(|| {
            DeviceError::new("SerialParse", "CHR length missing".to_string(), "Capture the trace for a bug report.")
        })?;
        let len = if first < 0x80 {
            first as usize
        } else if first == 0x81 {
            len_at += 1;
            *raw.get(len_at).ok_or_else(|| {
                DeviceError::new("SerialParse", "CHR long length truncated".to_string(), "Capture the trace for a bug report.")
            })? as usize
        } else if first == 0x82 {
            len_at += 2;
            let hi = *raw.get(len_at - 1).ok_or_else(|| {
                DeviceError::new("SerialParse", "CHR long length truncated".to_string(), "Capture the trace for a bug report.")
            })? as usize;
            let lo = *raw.get(len_at).ok_or_else(|| {
                DeviceError::new("SerialParse", "CHR long length truncated".to_string(), "Capture the trace for a bug report.")
            })? as usize;
            (hi << 8) | lo
        } else {
            return Err(DeviceError::new(
                "SerialParse",
                format!("Unsupported CHR length form {first:#04X}"),
                "Capture the trace for a bug report.",
            ));
        };
        if len == 0 || len > 64 {
            return Err(DeviceError::new(
                "SerialParse",
                format!("Implausible CHR length {len}"),
                "Capture the trace for a bug report.",
            ));
        }
        let bytes = raw.get(len_at + 1..len_at + 1 + len).ok_or_else(|| {
            DeviceError::new("SerialParse", "CHR value truncated".to_string(), "Capture the trace for a bug report.")
        })?;
        if !bytes.iter().all(|b| (0x20..0x7F).contains(b)) {
            return Err(DeviceError::new(
                "SerialParse",
                "CHR value is not printable ASCII".to_string(),
                "Capture the trace for a bug report.",
            ));
        }
        Ok(SerialInfo {
            serial: String::from_utf8_lossy(bytes).into_owned(),
            raw_len: raw.len(),
        })
    })
}
/// Minimal-length BER length encoding (short / 81 / 82, up to 65535).
/// Longer inputs truncate silently by construction — all callers stay far
/// below (largest: RSA-4096 CSRs ~700 bytes), so this is documented, not checked.
fn der_len(len: usize, out: &mut Vec<u8>) {
    if len < 128 {
        out.push(len as u8);
    } else if len < 256 {
        out.extend_from_slice(&[0x81, len as u8]);
    } else {
        out.extend_from_slice(&[0x82, (len >> 8) as u8, (len & 0xFF) as u8]);
    }
}

fn der_tlv(tag: u8, content: &[u8]) -> Vec<u8> {
    let mut out = vec![tag];
    der_len(content.len(), &mut out);
    out.extend_from_slice(content);
    out
}

fn der_oid(content: &[u8]) -> Vec<u8> {
    der_tlv(0x06, content)
}

fn der_integer(bytes: &[u8]) -> Vec<u8> {
    let mut v = bytes;
    while v.len() > 1 && v[0] == 0 {
        v = &v[1..];
    }
    let mut content = Vec::new();
    if !v.is_empty() && v[0] & 0x80 != 0 {
        content.push(0x00);
    }
    content.extend_from_slice(v);
    der_tlv(0x02, &content)
}

/// Named-curve OID contents (DER, without tag/len) for SPKI parameters.
/// NOTE: several curve tables coexist on purpose, each a different direction:
/// name→OID here, OID→name in curve_name_by_oid, prime→name in
/// curve_name_by_prime, name→bits in curve_bits, full domain in ec_domain.
fn named_curve_oid(name: &str) -> Option<&'static [u8]> {
    match name {
        "secp192r1" => Some(&[0x2B, 0x81, 0x04, 0x00, 0x21]),
        "secp256r1" => Some(&[0x2A, 0x86, 0x48, 0xCE, 0x3D, 0x03, 0x01, 0x07]),
        "secp384r1" => Some(&[0x2B, 0x81, 0x04, 0x00, 0x22]),
        "secp521r1" => Some(&[0x2B, 0x81, 0x04, 0x00, 0x23]),
        "secp192k1" => Some(&[0x2B, 0x81, 0x04, 0x00, 0x1F]),
        "secp256k1" => Some(&[0x2B, 0x81, 0x04, 0x00, 0x0A]),
        "brainpoolP256r1" => Some(&[0x2B, 0x24, 0x03, 0x03, 0x02, 0x08, 0x01, 0x01, 0x07]),
        "brainpoolP384r1" => Some(&[0x2B, 0x24, 0x03, 0x03, 0x02, 0x08, 0x01, 0x01, 0x0B]),
        "brainpoolP512r1" => Some(&[0x2B, 0x24, 0x03, 0x03, 0x02, 0x08, 0x01, 0x01, 0x0D]),
        "ed25519" => Some(&[0x2B, 0x65, 0x70]),
        "ed448" => Some(&[0x2B, 0x65, 0x71]),
        "curve25519" => Some(&[0x2B, 0x65, 0x6E]),
        "curve448" => Some(&[0x2B, 0x65, 0x6F]),
        _ => None,
    }
}

const OID_RSA_ENCRYPTION: [u8; 9] = [0x2A, 0x86, 0x48, 0x86, 0xF7, 0x0D, 0x01, 0x01, 0x01];
const OID_EC_PUBLIC_KEY: [u8; 7] = [0x2A, 0x86, 0x48, 0xCE, 0x3D, 0x02, 0x01];
/// EdDSA/XDH algorithm OIDs double as the curve name (no params in SPKI).
fn ed_curve_name(oid: &[u8]) -> Option<&'static str> {
    match oid {
        [0x2B, 0x65, 0x70] => Some("ed25519"),
        [0x2B, 0x65, 0x71] => Some("ed448"),
        [0x2B, 0x65, 0x6E] => Some("curve25519"),
        [0x2B, 0x65, 0x6F] => Some("curve448"),
        _ => None,
    }
}

/// Export the public key of a CVC certificate as PEM (SubjectPublicKeyInfo).
/// Reads the CVC blob, rebuilds standard DER. X.509 blobs are served by
/// `export_cert` (raw certificate download) instead.
#[tauri::command]
pub fn export_pubkey(reader: String, fid_hex: String, pin: Option<String>) -> Result<String, DeviceError> {
    let fid = fid_hex.trim().replace([' ', ':'], "");
    if fid.len() != 4 || !fid.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err(DeviceError::new(
            "BadFid",
            "FID must be 4 hex chars (e.g. CE01)".to_string(),
            "This is a frontend bug — please report.",
        ));
    }
    let hi = u8::from_str_radix(&fid[0..2], 16).unwrap_or(0);
    let lo = u8::from_str_radix(&fid[2..4], 16).unwrap_or(0);
    if hi != 0xCE && hi != 0xCA {
        return Err(DeviceError::new(
            "BadFid",
            "Only certificate files (CE/CA) have exportable public keys".to_string(),
            "Pick a certificate from the list.",
        ));
    }
    with_card(&reader, |card| {
        select_hsm(card)?;
        if let Some(ref p) = pin {
            verify_pin(card, p)?;
        }
        let cert = read_file_full(card, hi, lo)?.ok_or_else(|| {
            DeviceError::new(
                "NotFound",
                format!("Certificate {fid} does not exist"),
                "Reload the list.",
            )
        })?;
        if cert.first() == Some(&0x30) {
            return Err(DeviceError::new(
                "X509UseExportCert",
                "X.509 certificates download via the certificate action, not the public-key export".to_string(),
                "Use the certificate download for this entry.",
            ));
        }
        let pubkey = parse_cvc_pubkey(&cert).ok_or_else(|| {
            DeviceError::new(
                "CertParse",
                format!("Certificate {fid} has an unexpected CVC layout"),
                "Post the Diagnose details for analysis — nothing was changed.",
            )
        })?;
        let spki = spki_from_cvc(&pubkey)?;
        // base64 with 64-char PEM wrapping (implemented locally to avoid a new crate).
        Ok(pem_wrap(&spki, "PUBLIC KEY"))
    })
}

/// Parsed CVC public key: RSA (modulus/exponent) or EC (point, optional curve).
enum CvcPubkey {
    Rsa { modulus: Vec<u8>, exponent: Vec<u8> },
    Ec { point: Vec<u8>, curve: Option<&'static str> },
}

/// Named-curve name from DER OID contents (reverse of [`named_curve_oid`]).
fn curve_name_by_oid(oid: &[u8]) -> Option<&'static str> {
    match oid {
        [0x2B, 0x81, 0x04, 0x00, 0x21] => Some("secp192r1"),
        [0x2A, 0x86, 0x48, 0xCE, 0x3D, 0x03, 0x01, 0x07] => Some("secp256r1"),
        [0x2B, 0x81, 0x04, 0x00, 0x22] => Some("secp384r1"),
        [0x2B, 0x81, 0x04, 0x00, 0x23] => Some("secp521r1"),
        [0x2B, 0x81, 0x04, 0x00, 0x1F] => Some("secp192k1"),
        [0x2B, 0x81, 0x04, 0x00, 0x0A] => Some("secp256k1"),
        [0x2B, 0x24, 0x03, 0x03, 0x02, 0x08, 0x01, 0x01, 0x07] => Some("brainpoolP256r1"),
        [0x2B, 0x24, 0x03, 0x03, 0x02, 0x08, 0x01, 0x01, 0x0B] => Some("brainpoolP384r1"),
        [0x2B, 0x24, 0x03, 0x03, 0x02, 0x08, 0x01, 0x01, 0x0D] => Some("brainpoolP512r1"),
        _ => None,
    }
}

/// Parsed X.509 subject public key: RSA modulus bits or EC point (+ curve).
#[derive(Debug)]
enum X509Pubkey {
    Rsa { bits: u32 },
    Ec { point: Vec<u8>, curve: Option<&'static str> },
}

/// Validate an X.509 DER certificate structurally and extract the subject
/// public key (Batch B: validation + list display + key match).
/// Checks: outer SEQ covers the input and has exactly 3 children
/// (tbsCertificate SEQ, signatureAlgorithm SEQ, signatureValue BIT STRING);
/// the TBS contains a SubjectPublicKeyInfo SEQ (algorithm SEQ + BIT STRING);
/// the algorithm is rsaEncryption (inner SEQ of 2 INTEGERs) or ecPublicKey
/// (named-curve OID + uncompressed point). No signature/chain verification —
/// management display only, not trust validation.
fn parse_x509_pubkey(der: &[u8]) -> Option<X509Pubkey> {
    let outer = parse_tlv(der, 0)?;
    if outer.tag != 0x30 || !outer.constructed || outer.total.end != der.len() {
        return None;
    }
    let top = children(der, &outer.content)?;
    if top.len() != 3 {
        return None;
    }
    let (tbs, sig_alg, sig) = (&top[0], &top[1], &top[2]);
    if tbs.tag != 0x30 || !tbs.constructed {
        return None;
    }
    if sig_alg.tag != 0x30 || !sig_alg.constructed {
        return None;
    }
    if sig.tag != 0x03 || sig.constructed {
        return None;
    }
    // SPKI: TBS child SEQ of exactly { algorithm SEQ, BIT STRING }.
    let tbs_kids = children(der, &tbs.content)?;
    let spki = tbs_kids.iter().find(|t| {
        t.tag == 0x30
            && t.constructed
            && children(der, &t.content).is_some_and(|k| {
                k.len() == 2 && k[0].tag == 0x30 && k[0].constructed && k[1].tag == 0x03 && !k[1].constructed
            })
    })?;
    let spki_kids = children(der, &spki.content)?;
    let alg_kids = children(der, &spki_kids[0].content)?;
    let oid_t = alg_kids.iter().find(|t| t.tag == 0x06)?;
    let oid = &der[oid_t.content.clone()];
    let bit = &der[spki_kids[1].content.clone()];
    let (&unused, key_bytes) = bit.split_first()?;
    if unused != 0 {
        return None;
    }
    if oid == OID_RSA_ENCRYPTION {
        let rsa = parse_tlv(key_bytes, 0)?;
        if rsa.tag != 0x30 || !rsa.constructed || rsa.total.end != key_bytes.len() {
            return None;
        }
        let fields = children(key_bytes, &rsa.content)?;
        if fields.len() != 2 || fields[0].tag != 0x02 || fields[1].tag != 0x02 {
            return None;
        }
        Some(X509Pubkey::Rsa {
            bits: int_bits(&key_bytes[fields[0].content.clone()]),
        })
    } else if oid == OID_EC_PUBLIC_KEY {
        let curve = alg_kids
            .iter()
            .find(|t| t.tag == 0x06 && t.content != oid_t.content)
            .and_then(|t| curve_name_by_oid(&der[t.content.clone()]));
        if key_bytes.first() != Some(&0x04) {
            return None;
        }
        Some(X509Pubkey::Ec {
            point: key_bytes.to_vec(),
            curve,
        })
    } else if let Some(name) = ed_curve_name(oid) {
        // EdDSA/XDH: algorithm OID is the curve; BIT STRING holds raw bytes.
        Some(X509Pubkey::Ec {
            point: key_bytes.to_vec(),
            curve: Some(name),
        })
    } else {
        None
    }
}

/// Standard base64 encoder (PEM bodies; local to avoid a new crate).
fn base64_encode(data: &[u8]) -> String {
    const B64: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::new();
    for chunk in data.chunks(3) {
        let mut n: u32 = 0;
        for (i, &b) in chunk.iter().enumerate() {
            n |= (b as u32) << (16 - 8 * i);
        }
        let pad = 3 - chunk.len();
        for i in 0..4 - pad {
            out.push(B64[((n >> (18 - 6 * i)) & 63) as usize] as char);
        }
        for _ in 0..pad {
            out.push('=');
        }
    }
    out
}

/// Wrap DER bytes in a PEM block with 64-char lines.
fn pem_wrap(der: &[u8], label: &str) -> String {
    let mut pem = format!("-----BEGIN {label}-----\n");
    for line in base64_encode(der).as_bytes().chunks(64) {
        pem.push_str(std::str::from_utf8(line).unwrap_or(""));
        pem.push('\n');
    }
    pem.push_str(&format!("-----END {label}-----\n"));
    pem
}

/// Extract the public key from a CVC certificate blob (EE/CA files).
/// Layout (mirrors pypicohsm attestate): 7F49{ 06 OID, 81 modulus/prime,
/// 82 exponent, 86 EC point }. Returns None when absent/unparsable.
fn parse_cvc_pubkey(cert: &[u8]) -> Option<CvcPubkey> {
    let t7949 = find_tag(cert, &(0..cert.len()), 0x7F49, 0)?;
    let oid_t = find_tag(cert, &t7949.content, 0x06, 2)?;
    let oid = &cert[oid_t.content.clone()];
    if oid == OID_RSA_V15_SHA256 {
        let n = find_tag(cert, &t7949.content, 0x81, 2)?;
        let e = find_tag(cert, &t7949.content, 0x82, 2)?;
        Some(CvcPubkey::Rsa {
            modulus: cert[n.content.clone()].to_vec(),
            exponent: cert[e.content.clone()].to_vec(),
        })
    } else if oid == OID_ECDSA_SHA256 {
        let q = find_tag(cert, &t7949.content, 0x86, 2)?;
        let point = cert[q.content.clone()].to_vec();
        let curve = find_tag(&cert, &t7949.content, 0x81, 2)
            .and_then(|p| curve_name_by_prime(&cert[p.content.clone()]));
        Some(CvcPubkey::Ec { point, curve })
    } else {
        None
    }
}

/// Build a SubjectPublicKeyInfo DER from a parsed CVC public key.
/// Shared by the CVC public-key export and the CSR builder.
fn spki_from_cvc(pubkey: &CvcPubkey) -> Result<Vec<u8>, DeviceError> {
    match pubkey {
        CvcPubkey::Rsa { modulus, exponent } => {
            let mut rsa_seq = der_integer(modulus);
            rsa_seq.extend_from_slice(&der_integer(exponent));
            let rsa_seq = der_tlv(0x30, &rsa_seq);
            let mut alg = der_oid(&OID_RSA_ENCRYPTION);
            alg.extend_from_slice(&der_tlv(0x05, &[]));
            let alg = der_tlv(0x30, &alg);
            let mut bit = vec![0x00];
            bit.extend_from_slice(&rsa_seq);
            let bit = der_tlv(0x03, &bit);
            let mut spki = alg;
            spki.extend_from_slice(&bit);
            Ok(der_tlv(0x30, &spki))
        }
        CvcPubkey::Ec { point, curve } => {
            let curve_oid = curve.and_then(named_curve_oid).ok_or_else(|| {
                DeviceError::new(
                    "UnknownCurve",
                    "EC curve not recognized, cannot build SPKI parameters".to_string(),
                    "Post the Diagnose details for analysis.",
                )
            })?;
            let mut alg = der_oid(&OID_EC_PUBLIC_KEY);
            alg.extend_from_slice(&der_oid(curve_oid));
            let alg = der_tlv(0x30, &alg);
            let mut bit = vec![0x00];
            bit.extend_from_slice(point);
            let bit = der_tlv(0x03, &bit);
            let mut spki = alg;
            spki.extend_from_slice(&bit);
            Ok(der_tlv(0x30, &spki))
        }
    }
}

/// Parse a SubjectPublicKeyInfo DER (RSA/ECDSA only, no EdDSA/XDH).
/// Used to validate a caller-supplied SPKI (generation-record fallback).
/// Returns (key_type, size_bits, curve).
fn parse_spki_pubkey(spki: &[u8]) -> Option<(String, u32, Option<String>)> {
    let outer = parse_tlv(spki, 0)?;
    if outer.tag != 0x30 || !outer.constructed || outer.total.end != spki.len() {
        return None;
    }
    let kids = children(spki, &outer.content)?;
    if kids.len() != 2 || kids[0].tag != 0x30 || !kids[0].constructed {
        return None;
    }
    if kids[1].tag != 0x03 || kids[1].constructed {
        return None;
    }
    let alg_kids = children(spki, &kids[0].content)?;
    let oid_t = alg_kids.iter().find(|t| t.tag == 0x06)?;
    let oid = &spki[oid_t.content.clone()];
    let bit = &spki[kids[1].content.clone()];
    let (&unused, key_bytes) = bit.split_first()?;
    if unused != 0 {
        return None;
    }
    if oid == OID_RSA_ENCRYPTION {
        let rsa = parse_tlv(key_bytes, 0)?;
        if rsa.tag != 0x30 || !rsa.constructed || rsa.total.end != key_bytes.len() {
            return None;
        }
        let fields = children(key_bytes, &rsa.content)?;
        if fields.len() != 2 || fields[0].tag != 0x02 || fields[1].tag != 0x02 {
            return None;
        }
        Some(("RSA".to_string(), int_bits(&key_bytes[fields[0].content.clone()]), None))
    } else if oid == OID_EC_PUBLIC_KEY {
        let curve = alg_kids
            .iter()
            .find(|t| t.tag == 0x06 && t.content != oid_t.content)
            .and_then(|t| curve_name_by_oid(&spki[t.content.clone()]));
        if key_bytes.first() != Some(&0x04) {
            return None;
        }
        let bits = curve.and_then(curve_bits).or_else(|| ec_point_bits(key_bytes))?;
        Some(("EC".to_string(), bits, curve.map(|s| s.to_string())))
    } else {
        None
    }
}

const OID_SHA256_RSA: [u8; 9] = [0x2A, 0x86, 0x48, 0x86, 0xF7, 0x0D, 0x01, 0x01, 0x0B];
const OID_ECDSA_SHA256_X509: [u8; 8] = [0x2A, 0x86, 0x48, 0xCE, 0x3D, 0x04, 0x03, 0x02];
const OID_AT_CN: [u8; 3] = [0x55, 0x04, 0x03];
const OID_AT_O: [u8; 3] = [0x55, 0x04, 0x0A];
const OID_AT_OU: [u8; 3] = [0x55, 0x04, 0x0B];
const OID_AT_C: [u8; 3] = [0x55, 0x04, 0x06];

/// One RelativeDistinguishedName: SET{ SEQ{ OID, string } }.
fn rdn(oid: &[u8], tag: u8, value: &str) -> Vec<u8> {
    let mut atv = der_oid(oid);
    atv.extend_from_slice(&der_tlv(tag, value.as_bytes()));
    der_tlv(0x31, &der_tlv(0x30, &atv))
}

/// Build a PKCS#10 CertificationRequestInfo DER: SEQ{ INTEGER 0, subject
/// RDNSequence, SPKI, [0] EXPLICIT empty attributes }. Empty DN parts are
/// skipped (CN required). The empty attributes element keeps strict parsers
/// happy (OpenSSL emits it too).
/// Returns Err when a value is out of range (printable, length-capped).
fn build_tbs_csr(cn: &str, o: &str, ou: &str, c: &str, spki: &[u8]) -> Result<Vec<u8>, DeviceError> {
    let bad = |msg: &str| {
        DeviceError::new(
            "BadSubject",
            msg.to_string(),
            "Use 1-64 chars (CN required); C is 2 letters or empty.",
        )
    };
    let printable = |s: &str| !s.is_empty() && s.len() <= 64 && s.bytes().all(|b| (0x20..0x7F).contains(&b));
    if !printable(cn) {
        return Err(bad("Common Name must be 1-64 printable ASCII characters."));
    }
    for (name, v) in [("O", o), ("OU", ou)] {
        if !v.is_empty() && !printable(v) {
            return Err(bad(format!("{name} must be 1-64 printable ASCII characters.").as_str()));
        }
    }
    if !c.is_empty() && (c.len() != 2 || !c.bytes().all(|b| b.is_ascii_alphabetic())) {
        return Err(bad("Country must be 2 letters or empty."));
    }
    let mut subject = rdn(&OID_AT_CN, 0x0C, cn);
    if !o.is_empty() {
        subject.extend_from_slice(&rdn(&OID_AT_O, 0x0C, o));
    }
    if !ou.is_empty() {
        subject.extend_from_slice(&rdn(&OID_AT_OU, 0x0C, ou));
    }
    if !c.is_empty() {
        subject.extend_from_slice(&rdn(&OID_AT_C, 0x13, &c.to_uppercase()));
    }
    let subject = der_tlv(0x30, &subject);
    let mut tbs = der_tlv(0x02, &[0x00]);
    tbs.extend_from_slice(&subject);
    tbs.extend_from_slice(spki);
    // Empty [0] EXPLICIT attributes (a0 00), like OpenSSL emits.
    tbs.extend_from_slice(&[0xA0, 0x00]);
    Ok(der_tlv(0x30, &tbs))
}

/// PKCS#15 key usage from the description record's BIT STRING
/// (second inner SEQ: OCTET id + BIT STRING; verified against
/// pkcs15-tool output: RSA `03 02 [02]74` -> 0x2E, AES `03 03 [07]C0 10`
/// -> 0x0803, EC `03 03 [07]20 80` -> 0x0104).
/// Rule: reverse bits per content byte (after the unused-count byte),
/// assemble little-endian (BER requires unused bits zeroed).
/// Static per key type — NOT the configured GAK-0x91 restrictions.
fn parse_usage(raw: &[u8]) -> Option<u32> {
    let outer = parse_tlv(raw, 0)?;
    if !outer.constructed {
        return None;
    }
    let kids = children(raw, &outer.content)?;
    let usage_seq = kids.get(1).filter(|t| t.tag == 0x30 && t.constructed)?;
    let fields = children(raw, &usage_seq.content)?;
    let bs = fields.iter().find(|t| t.tag == 0x03 && !t.constructed)?;
    let content = &raw[bs.content.clone()];
    let (&_unused, data) = content.split_first()?;
    let mut value: u32 = 0;
    for (i, &b) in data.iter().enumerate().take(4) {
        value |= (b.reverse_bits() as u32) << (8 * i);
    }
    Some(value)
}

/// Bit length of a big-endian integer (strip leading zero bytes).
fn int_bits(bytes: &[u8]) -> u32 {
    let mut b = bytes;
    while b.len() > 1 && b[0] == 0 {
        b = &b[1..];
    }
    if b.is_empty() {
        return 0;
    }
    (b.len() as u32 - 1) * 8 + (8 - b[0].leading_zeros())
}

/// Bit length of an uncompressed EC point (04||X||Y); None otherwise.
fn ec_point_bits(point: &[u8]) -> Option<u32> {
    if point.first() == Some(&0x04) && point.len() >= 3 && point.len() % 2 == 1 {
        Some(((point.len() - 1) / 2 * 8) as u32)
    } else {
        None
    }
}

#[derive(Debug, Serialize, Clone)]
pub struct CertEntry {
    /// File id, e.g. "CE01" (key cert) or "CA02" (standalone CA cert).
    pub fid: String,
    /// ee (key certificate) | ca (standalone).
    pub kind: String,
    /// Key id for ee certs.
    pub id: u8,
    pub label: Option<String>,
    /// RSA | EC | X.509 | unknown. X.509 marks a stored X.509 blob whose
    /// subject public key could not be parsed (or exotic algorithms).
    pub key_type: String,
    pub size_bits: Option<u32>,
    pub curve: Option<String>,
    /// Blob format: "cvc" | "x509" | "unknown" (unknown = stored but unparsable).
    pub format: String,
    /// False when no CE file is stored (firmware container generation).
    pub has_cert: bool,
}

/// Classify a stored certificate blob for list display.
/// CVC (parsed public key) -> "cvc"; structurally valid X.509 -> "x509"
/// (with RSA/EC details when the SPKI parses); anything else -> "unknown".
fn cert_blob_info(blob: &[u8]) -> (String, String, Option<u32>, Option<String>) {
    if blob.first() == Some(&0x30) {
        if let Some(pk) = parse_x509_pubkey(blob) {
            return match pk {
                X509Pubkey::Rsa { bits } => ("x509".to_string(), "RSA".to_string(), Some(bits), None),
                X509Pubkey::Ec { point, curve } => {
                    let bits = curve
                        .and_then(curve_bits)
                        .or_else(|| ec_point_bits(&point));
                    (
                        "x509".to_string(),
                        "EC".to_string(),
                        bits,
                        curve.map(|s| s.to_string()),
                    )
                }
            };
        }
        return ("x509".to_string(), "X.509".to_string(), None, None);
    }
    match parse_cvc_pubkey(blob) {
        Some(CvcPubkey::Rsa { modulus, .. }) => (
            "cvc".to_string(),
            "RSA".to_string(),
            Some(int_bits(&modulus)),
            None,
        ),
        Some(CvcPubkey::Ec { point, curve: c }) => {
            let bits = c.and_then(curve_bits).or_else(|| ec_point_bits(&point));
            (
                "cvc".to_string(),
                "EC".to_string(),
                bits,
                c.map(|s| s.to_string()),
            )
        }
        None => ("unknown".to_string(), "unknown".to_string(), None, None),
    }
}

/// List certificates with parsed summaries.
/// ENUMERATE OBJECTS omits CE/CA by firmware design, so EE certs are probed
/// per known key id (`CE<id>`); CA files can only appear via LIST (kept as
/// a defensive branch). Keys without a stored cert get an "ee" row with
/// empty details so the absence is visible instead of silent.
#[tauri::command]
pub fn list_certs(reader: String) -> Result<Vec<CertEntry>, DeviceError> {
    with_card(&reader, |card| {
        select_hsm(card)?;
        let rsp = transmit_on_card(card, &[0x80, 0x58, 0x00, 0x00, 0x00])?;
        if rsp.sw_hex != "9000" {
            return Err(DeviceError::new(
                "ListFailed",
                format!("ENUMERATE OBJECTS failed with SW={}", rsp.sw_hex),
                "Check firmware version (>= 5.x expected for INS 0x58).",
            ));
        }
        let raw = hex::decode(&rsp.data_hex).unwrap_or_default();
        // Key ids present on device (any CC/C4/CE prefix).
        let mut ids: Vec<u8> = raw
            .chunks_exact(2)
            .filter(|p| !(p[0] == 0x00 && p[1] == 0x00))
            .filter(|p| p[0] == 0xCC || p[0] == 0xC4 || p[0] == 0xCE)
            .map(|p| p[1])
            .collect();
        ids.sort();
        ids.dedup();
        let mut out = Vec::new();
        for id in ids {
            if id == 0 {
                continue;
            }
            // Label: inherit PRKD when present.
            let mut label: Option<String> = None;
            if let Ok(Some(prkd)) = read_file_full(card, 0xC4, id) {
                label = parse_label(&prkd);
            }
            // Pubkey summary from the EE cert blob itself (6A82 = none stored).
            let mut key_type = "unknown".to_string();
            let mut size_bits: Option<u32> = None;
            let mut curve: Option<String> = None;
            let mut blob_format = "unknown".to_string();
            let mut has_cert = false;
            if let Ok(Some(cert)) = read_file_full(card, 0xCE, id) {
                has_cert = true;
                (blob_format, key_type, size_bits, curve) = cert_blob_info(&cert);
            }
            out.push(CertEntry {
                fid: format!("CE{id:02X}"),
                kind: "ee".to_string(),
                id,
                label,
                key_type,
                size_bits,
                curve,
                format: blob_format,
                has_cert,
            });
        }
        // Defensive: CA files if LIST ever reports them (currently omitted by firmware).
        for pair in raw.chunks_exact(2) {
            let (hi, lo) = (pair[0], pair[1]);
            if hi != 0xCA || (hi == 0x00 && lo == 0x00) {
                continue;
            }
            let mut label: Option<String> = None;
            if let Ok(Some(cd)) = read_file_full(card, 0xC8, lo) {
                label = parse_label(&cd);
            }
            let mut key_type = "unknown".to_string();
            let mut size_bits: Option<u32> = None;
            let mut curve: Option<String> = None;
            let mut blob_format = "unknown".to_string();
            let mut has_cert = false;
            if let Ok(Some(cert)) = read_file_full(card, hi, lo) {
                has_cert = true;
                (blob_format, key_type, size_bits, curve) = cert_blob_info(&cert);
            }
            out.push(CertEntry {
                fid: format!("{hi:02X}{lo:02X}"),
                kind: "ca".to_string(),
                id: lo,
                label,
                key_type,
                size_bits,
                curve,
                format: blob_format,
                has_cert,
            });
        }
        out.sort_by(|a, b| (a.kind.clone(), a.id).cmp(&(b.kind.clone(), b.id)));
        Ok(out)
    })
}

/// Delete a standalone CA certificate (`00 E4`, auth required).
/// EE certs (CE) are deleted with their key group (Phase 3) — never alone.
#[tauri::command]
pub fn delete_cert(reader: String, fid_hex: String, pin: Option<String>) -> Result<String, DeviceError> {
    let fid = fid_hex.trim().replace([' ', ':'], "");
    if fid.len() != 4 || !fid.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err(DeviceError::new(
            "BadFid",
            "FID must be 4 hex chars (e.g. CA02)".to_string(),
            "This is a frontend bug — please report.",
        ));
    }
    let hi = u8::from_str_radix(&fid[0..2], 16).unwrap_or(0);
    let lo = u8::from_str_radix(&fid[2..4], 16).unwrap_or(0);
    if hi != 0xCA {
        return Err(DeviceError::new(
            "RefusedDelete",
            "Only standalone CA certificates can be deleted here".to_string(),
            "Key certificates (CE) are deleted with their key group in the Keys tab.",
        ));
    }
    with_card(&reader, |card| {
        select_hsm(card)?;
        if let Some(ref p) = pin {
            verify_pin(card, p)?;
        }
        let rsp = transmit_on_card(card, &[0x00, 0xE4, 0x00, 0x00, 0x02, hi, lo, 0x00])?;
        if rsp.sw_hex == "6A82" {
            return Err(DeviceError::new(
                "NotFound",
                format!("Certificate {fid} does not exist"),
                "Reload the list.",
            ));
        }
        if rsp.sw_hex == "6982" {
            return Err(DeviceError::auth_required(
                "DeleteAuth",
                "Delete needs User-PIN authentication (SW=6982)".to_string(),
            ));
        }
        if rsp.sw_hex != "9000" {
            return Err(DeviceError::new(
                "DeleteFailed",
                format!("Delete of {fid} failed with SW={}", rsp.sw_hex),
                "Retry; if persistent, capture the trace.",
            ));
        }
        Ok(format!("Certificate {fid} deleted."))
    })
}

/// Decode standard base64 (for PEM bodies). Whitespace-tolerant, strict alphabet.
fn base64_decode(input: &str) -> Option<Vec<u8>> {
    fn val(c: u8) -> Option<u8> {
        match c {
            b'A'..=b'Z' => Some(c - b'A'),
            b'a'..=b'z' => Some(c - b'a' + 26),
            b'0'..=b'9' => Some(c - b'0' + 52),
            b'+' => Some(62),
            b'/' => Some(63),
            _ => None,
        }
    }
    let clean: Vec<u8> = input.bytes().filter(|b| !b.is_ascii_whitespace()).collect();
    if clean.is_empty() || clean.len() % 4 != 0 {
        return None;
    }
    let mut out = Vec::with_capacity(clean.len() / 4 * 3);
    for chunk in clean.chunks_exact(4) {
        let mut pad = 0;
        let mut n: u32 = 0;
        for (i, &c) in chunk.iter().enumerate() {
            if c == b'=' {
                pad += 1;
                n <<= 6;
            } else {
                if pad > 0 {
                    return None;
                }
                n = (n << 6) | val(c)? as u32;
            }
            let _ = i;
        }
        if pad > 2 {
            return None;
        }
        out.push((n >> 16) as u8);
        if pad < 2 {
            out.push((n >> 8) as u8);
        }
        if pad < 1 {
            out.push(n as u8);
        }
    }
    Some(out)
}

/// Extract DER bytes from file content: raw DER or PEM certificate block.
/// Structural X.509 validation (outer cert + RSA/EC subject public key) is
/// required — arbitrary ASN.1 SEQUENCEs are rejected before any device write.
fn extract_der_cert(data: &[u8]) -> Result<Vec<u8>, DeviceError> {
    let bad = |msg: &str| {
        DeviceError::new(
            "BadCert",
            msg.to_string(),
            "Provide a DER file or a PEM file with a CERTIFICATE block.",
        )
    };
    let der: Vec<u8>;
    // Raw DER: must start with constructed SEQUENCE.
    if data.first() == Some(&0x30) {
        der = data.to_vec();
    } else {
        // PEM: first CERTIFICATE block.
        let text = std::str::from_utf8(data).map_err(|_| bad("Not DER and not valid UTF-8 PEM text."))?;
        let begin = text.find("-----BEGIN CERTIFICATE-----").ok_or_else(|| bad("No CERTIFICATE block found."))?;
        let after = &text[begin + "-----BEGIN CERTIFICATE-----".len()..];
        let end = after.find("-----END CERTIFICATE-----").ok_or_else(|| bad("Unterminated CERTIFICATE block."))?;
        der = base64_decode(&after[..end]).ok_or_else(|| bad("CERTIFICATE block is not valid base64."))?;
        if der.len() < 16 {
            return Err(bad("Decoded PEM block is too short to be a certificate."));
        }
    }
    match parse_x509_pubkey(&der) {
        Some(X509Pubkey::Rsa { .. }) | Some(X509Pubkey::Ec { .. }) => Ok(der),
        None => Err(bad("Not a structurally valid X.509 certificate (RSA/EC).")),
    }
}

/// Encode one UPDATE EF chunk (`00 D7 <hi> <lo>` +
/// `[54 02 off][53 len data]`). Pure helper so chunk math is unit-testable.
fn update_payload(hi: u8, lo: u8, offset: usize, chunk: &[u8]) -> Option<Vec<u8>> {
    if chunk.is_empty() || chunk.len() > 200 || offset > 0xFFFF {
        return None;
    }
    let off = offset as u16;
    let mut payload = vec![0x54, 0x02, (off >> 8) as u8, (off & 0xFF) as u8, 0x53];
    // Length encoding supports short + 81/82 (chunks are small anyway).
    if chunk.len() < 128 {
        payload.push(chunk.len() as u8);
    } else {
        payload.push(0x81);
        payload.push(chunk.len() as u8);
    }
    payload.extend_from_slice(chunk);
    if payload.len() > 255 {
        return None;
    }
    let mut apdu = vec![0x00, 0xD7, hi, lo, payload.len() as u8];
    apdu.extend_from_slice(&payload);
    apdu.push(0x00);
    Some(apdu)
}

/// Write bytes to a file in offset chunks (`00 D7 <hi> <lo>` +
/// `[54 02 off][53 len data]`), like OpenSC sc_hsm_write_ef. Cap 8 KiB.
fn write_file_chunked(
    card: &pcsc::Card,
    hi: u8,
    lo: u8,
    data: &[u8],
    what: &str,
) -> Result<(), DeviceError> {
    if data.is_empty() || data.len() > 8192 {
        return Err(DeviceError::new(
            "BadCert",
            format!("{what} size out of range (1-8192 bytes)"),
            "Provide a normal certificate file.",
        ));
    }
    let mut offset: usize = 0;
    while offset < data.len() {
        let take = (data.len() - offset).min(200);
        let apdu = update_payload(hi, lo, offset, &data[offset..offset + take]).ok_or_else(|| {
            DeviceError::new(
                "BadCert",
                "Chunk encoding overflow".to_string(),
                "This is a frontend bug — please report.",
            )
        })?;
        let rsp = transmit_on_card(card, &apdu)?;
        if rsp.sw_hex == "6982" {
            return Err(DeviceError::auth_required(
                "ImportAuth",
                format!("{what} needs User-PIN authentication (SW=6982)"),
            ));
        }
        if rsp.sw_hex != "9000" {
            return Err(DeviceError::new(
                "ImportFailed",
                format!("{what} failed with SW={} at offset {offset}", rsp.sw_hex),
                "Nothing further was written. The file may be partially written — retry or delete it.",
            ));
        }
        offset += take;
    }
    Ok(())
}

/// Import an X.509/DER certificate onto a key id (`CE<id>`, erase + write).
/// Safety: the file is fully validated before any device write; the target
/// key must exist; identical content is a no-op; after writing, the file is
/// read back and must match byte-for-byte. On failure after erase, the
/// previous (public) bytes are restored best-effort. Needs User-PIN.
/// Overwriting an existing cert is the caller's decision (UI confirms).
#[tauri::command]
pub fn import_cert(
    reader: String,
    id: u8,
    file_bytes: Vec<u8>,
    pin: Option<String>,
) -> Result<String, DeviceError> {
    if id == 0 {
        return Err(DeviceError::new(
            "RefusedImport",
            "Cannot import onto the device key (ID 0)".to_string(),
            "Pick a user key id.",
        ));
    }
    if file_bytes.len() > 8192 {
        return Err(DeviceError::new(
            "BadCert",
            "File exceeds 8192 bytes".to_string(),
            "Provide a normal DER/PEM certificate.",
        ));
    }
    let der = extract_der_cert(&file_bytes)?;
    // Target key must exist (PRKD description or key blob); checked before
    // any destructive step, in a separate session.
    let details = key_details(reader.clone(), id).map_err(|_| {
        DeviceError::new(
            "KeyMissing",
            format!("No key with id {id} on the device"),
            "Generate or pick an existing key first.",
        )
    })?;
    // The certificate should belong to the on-device key: compare type and
    // size/curve when the device reports them (unknown skips the check).
    if let Some(pk) = parse_x509_pubkey(&der) {
        let matches = match pk {
            X509Pubkey::Rsa { bits } => {
                details.key_type == "RSA" && details.size_bits.is_none_or(|s| s == bits)
            }
            X509Pubkey::Ec { curve, .. } => {
                details.key_type == "EC"
                    && (details.curve.is_none()
                        || curve.is_none_or(|c| Some(c.to_string()) == details.curve))
            }
        };
        if details.key_type != "unknown" && !matches {
            return Err(DeviceError::new(
                "CertKeyMismatch",
                "Certificate does not match the on-device key (type/size/curve)".to_string(),
                "Import the certificate issued for this key id.",
            ));
        }
    }
    with_card(&reader, |card| {
        select_hsm(card)?;
        if let Some(ref p) = pin {
            verify_pin(card, p)?;
        }
        // Previous bytes (public cert) for no-op detection and restore.
        let previous = read_file_full(card, 0xCE, id)?;
        if previous.as_deref() == Some(der.as_slice()) {
            return Ok(format!("Certificate CE{id:02X} already stores these bytes — nothing changed."));
        }
        // Erase-first like OpenSC (update path with erase=1): delete, tolerate absent.
        let del = transmit_on_card(card, &[0x00, 0xE4, 0x00, 0x00, 0x02, 0xCE, id, 0x00])?;
        if del.sw_hex == "6982" {
            return Err(DeviceError::auth_required(
                "ImportAuth",
                "Import needs User-PIN authentication (SW=6982)".to_string(),
            ));
        }
        if del.sw_hex != "9000" && del.sw_hex != "6A82" {
            return Err(DeviceError::new(
                "ImportFailed",
                format!("Erase of CE{id:02X} failed with SW={}", del.sw_hex),
                "Nothing was written. Retry; if persistent, capture the trace.",
            ));
        }
        let restore = |card: &pcsc::Card, old: &Option<Vec<u8>>| {
            if let Some(prev) = old {
                let _ = write_file_chunked(card, 0xCE, id, prev, "Certificate restore");
            }
        };
        if let Err(e) = write_file_chunked(card, 0xCE, id, &der, "Certificate import") {
            restore(card, &previous);
            return Err(e);
        }
        // Read-back verification: stored bytes must equal the import.
        match read_file_full(card, 0xCE, id)? {
            Some(stored) if stored == der => Ok(format!(
                "Certificate ({} bytes) imported as CE{id:02X}.",
                der.len()
            )),
            _ => {
                restore(card, &previous);
                Err(DeviceError::new(
                    "ImportVerify",
                    "Stored certificate differs from the import after write".to_string(),
                    "Previous content was restored when present. Retry; if persistent, capture the trace.",
                ))
            }
        }
    })
}

/// Download a stored X.509 certificate as PEM (raw bytes, byte-identical).
/// CVC blobs stay with `export_pubkey` (SPKI rebuild); anything else errors.
#[tauri::command]
pub fn export_cert(reader: String, fid_hex: String, pin: Option<String>) -> Result<String, DeviceError> {
    let fid = fid_hex.trim().replace([' ', ':'], "");
    if fid.len() != 4 || !fid.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err(DeviceError::new(
            "BadFid",
            "FID must be 4 hex chars (e.g. CE01)".to_string(),
            "This is a frontend bug — please report.",
        ));
    }
    let hi = u8::from_str_radix(&fid[0..2], 16).unwrap_or(0);
    let lo = u8::from_str_radix(&fid[2..4], 16).unwrap_or(0);
    if hi != 0xCE && hi != 0xCA {
        return Err(DeviceError::new(
            "BadFid",
            "Only certificate files (CE/CA) can be downloaded".to_string(),
            "Pick a certificate from the list.",
        ));
    }
    with_card(&reader, |card| {
        select_hsm(card)?;
        if let Some(ref p) = pin {
            verify_pin(card, p)?;
        }
        let cert = read_file_full(card, hi, lo)?.ok_or_else(|| {
            DeviceError::new(
                "NotFound",
                format!("Certificate {fid} does not exist"),
                "Reload the list.",
            )
        })?;
        if cert.first() != Some(&0x30) || parse_x509_pubkey(&cert).is_none() {
            return Err(DeviceError::new(
                "NotX509",
                format!("Certificate {fid} is not a stored X.509 blob"),
                "CVC entries export via the public-key action.",
            ));
        }
        Ok(pem_wrap(&cert, "CERTIFICATE"))
    })
}

/// Build a PKCS#10 certificate signing request for an on-device key.
///
/// Flow (mirrors the `openssl req -engine pkcs11` model): the TBS
/// (CertificationRequestInfo) is assembled off-device; the device signs the
/// raw TBS with `00 68 <id> <algo>` (the card hashes internally for
/// ALGO_RSA_PKCS1_SHA256 0x33 / ALGO_EC_SHA256 0x73), and the CSR is
/// assembled + returned as PEM. Needs User-PIN; each signature consumes one
/// key-counter step when a limit is configured.
///
/// Public key sources: the stored CVC certificate (`CE<id>`, authoritative)
/// or a caller-supplied SPKI hex (captured by this app at generation time
/// for keys without a stored cert). RSA + ECDSA only; AES/EdDSA/XDH and
/// unknown curves are refused honestly.
#[tauri::command]
pub fn export_csr(
    reader: String,
    id: u8,
    spki_hex: Option<String>,
    cn: String,
    o: String,
    ou: String,
    c: String,
    pin: Option<String>,
) -> Result<String, DeviceError> {
    if id == 0 {
        return Err(DeviceError::new(
            "RefusedCsr",
            "No CSR for the device key (ID 0)".to_string(),
            "Pick a user key id.",
        ));
    }
    // Target key must exist (separate session, like import_cert); the details
    // drive the key-type gate and the key-match below.
    let details = key_details(reader.clone(), id).map_err(|_| {
        DeviceError::new(
            "KeyMissing",
            format!("No key with id {id} on the device"),
            "Generate or pick an existing key first.",
        )
    })?;
    if details.key_type != "RSA" && details.key_type != "EC" {
        return Err(DeviceError::new(
            "RefusedCsr",
            format!("Key {id} is {} — CSR needs RSA or EC", details.key_type),
            "AES keys have no public key; EdDSA/XDH are not supported in this version.",
        ));
    }
    with_card(&reader, |card| {
        select_hsm(card)?;
        if let Some(ref p) = pin {
            verify_pin(card, p)?;
        }
        // SPKI: stored CVC first, caller-supplied (generation record) fallback.
        let spki: Vec<u8>;
        let (key_type, size_bits, curve) = match read_file_full(card, 0xCE, id)? {
            Some(cert) if cert.first() != Some(&0x30) => match parse_cvc_pubkey(&cert) {
                Some(pk) => {
                    let built = spki_from_cvc(&pk)?;
                    let summary = parse_spki_pubkey(&built).ok_or_else(|| {
                        DeviceError::new(
                            "CertParse",
                            format!("Stored certificate CE{id:02X} has an unusable public key"),
                            "Post the Diagnose details for analysis.",
                        )
                    })?;
                    spki = built;
                    summary
                }
                None => {
                    return Err(DeviceError::new(
                        "CertParse",
                        format!("Stored certificate CE{id:02X} has an unexpected CVC layout"),
                        "Post the Diagnose details for analysis — nothing was changed.",
                    ))
                }
            },
            _ => {
                let hex = spki_hex.as_deref().ok_or_else(|| {
                    DeviceError::new(
                        "NoPubkey",
                        format!("No on-device public key for key {id} (no stored certificate, no generation record)"),
                        "Keys generated outside this app need a stored certificate first; or regenerate the key here.",
                    )
                })?;
                let bytes = hex::decode(hex.trim()).map_err(|_| {
                    DeviceError::new(
                        "BadSpki",
                        "Supplied SPKI is not valid hex".to_string(),
                        "This is a frontend bug — please report.",
                    )
                })?;
                let summary = parse_spki_pubkey(&bytes).ok_or_else(|| {
                    DeviceError::new(
                        "BadSpki",
                        "Supplied SPKI is not a valid RSA/ECDSA public key".to_string(),
                        "Regenerate the key in this app to refresh the record.",
                    )
                })?;
                spki = bytes;
                summary
            }
        };
        // The key must match the device's own view (type + size/curve).
        let matches = key_type == details.key_type
            && details.size_bits.is_none_or(|s| s == size_bits)
            && (details.curve.is_none() || curve.is_none() || details.curve == curve);
        if !matches {
            return Err(DeviceError::new(
                "CertKeyMismatch",
                "Public key does not match the on-device key (type/size/curve)".to_string(),
                "Reload and retry; if persistent, capture the trace.",
            ));
        }
        let tbs = build_tbs_csr(cn.trim(), o.trim(), ou.trim(), c.trim(), &spki)?;
        // TBS carries the SPKI (RSA-4096 ≈ 550 bytes), so short APDU rarely
        // fits: extended form via case4 (proven by secp521r1 GAK). Cap 1 KiB
        // for the card-side APDU buffer; longer is refused honestly.
        if tbs.len() > 1024 {
            return Err(DeviceError::new(
                "SubjectTooLong",
                format!("Request info is {} bytes (cap 1024)", tbs.len()),
                "Use shorter subject fields.",
            ));
        }
        // The card hashes the raw TBS internally (SHA-256 for both algos).
        let algo: u8 = if key_type == "RSA" { 0x33 } else { 0x73 };
        let apdu = case4(0x00, 0x68, id, algo, &tbs).ok_or_else(|| {
            DeviceError::new(
                "SignFailed",
                "Could not encode signature APDU".to_string(),
                "This is a frontend bug — please report.",
            )
        })?;
        let rsp = transmit_on_card(card, &apdu)?;
        if rsp.sw_hex == "6982" {
            return Err(DeviceError::auth_required(
                "CsrAuth",
                "CSR signing needs User-PIN authentication (SW=6982)".to_string(),
            ));
        }
        if rsp.sw_hex == "6985" {
            return Err(DeviceError::new(
                "SignNotAllowed",
                "Key refuses signing with this algorithm (SW=6985)".to_string(),
                "The key's purpose restrictions or counter forbid it. Check the key details.",
            ));
        }
        if rsp.sw_hex == "6A84" {
            return Err(DeviceError::new(
                "CounterExhausted",
                "Key usage counter is exhausted (SW=6A84)".to_string(),
                "This key cannot sign anymore.",
            ));
        }
        if rsp.sw_hex != "9000" {
            return Err(DeviceError::new(
                "SignFailed",
                format!("Device signature failed with SW={}", rsp.sw_hex),
                "Nothing was changed. Retry; if persistent, capture the trace.",
            ));
        }
        let sig = hex::decode(&rsp.data_hex).unwrap_or_default();
        if sig.is_empty() {
            return Err(DeviceError::new(
                "SignFailed",
                "Device returned an empty signature".to_string(),
                "Retry; if persistent, capture the trace for a bug report.",
            ));
        }
        // CSR: SEQ{ TBS, SEQ{ sigAlg OID (+ NULL for RSA) }, BITSTRING{00 + sig} }.
        Ok(pem_wrap(&assemble_csr(&tbs, &key_type, &sig), "CERTIFICATE REQUEST"))
    })
}

/// Assemble a PKCS#10 CertificationRequest DER from TBS + raw signature.
/// Pure helper so the envelope shape is unit-testable.
fn assemble_csr(tbs: &[u8], key_type: &str, sig: &[u8]) -> Vec<u8> {
    let mut sig_alg = der_oid(if key_type == "RSA" { &OID_SHA256_RSA } else { &OID_ECDSA_SHA256_X509 });
    if key_type == "RSA" {
        sig_alg.extend_from_slice(&der_tlv(0x05, &[]));
    }
    let sig_alg = der_tlv(0x30, &sig_alg);
    let mut bit = vec![0x00];
    bit.extend_from_slice(sig);
    let bit = der_tlv(0x03, &bit);
    let mut csr = tbs.to_vec();
    csr.extend_from_slice(&sig_alg);
    csr.extend_from_slice(&bit);
    der_tlv(0x30, &csr)
}

/// One file entry from ENUMERATE OBJECTS (`80 58`).
#[derive(Debug, Serialize, Clone)]
pub struct KeyEntry {
    /// File id as 4 uppercase hex chars, e.g. "CC01".
    pub fid: String,
    /// High byte meaning: key (CC), prkd (C4), cert (C8/C9/CE/CA), unknown.
    pub kind: String,
    /// Low byte (key reference / id).
    pub id: u8,
    /// Label from the description record (PRKD/CD/DCOD), if any.
    /// EE certs (CE) inherit the same-id PRKD label; id 0 is "Device key".
    pub label: Option<String>,
    /// Raw bytes of the description record (details popup + field mapping).
    pub desc_hex: Option<String>,
    /// Raw bytes of the whole list response (for Diagnose).
    pub raw_hex: String,
}

fn classify_fid(hi: u8) -> &'static str {
    match hi {
        0xCC => "key",
        0xC4 => "prkd",
        0xC8 => "cert",
        0xC9 => "cert",
        0xCE => "cert",
        0xCA => "cert",
        _ => "unknown",
    }
}

/// Read a whole transparent file via READ BINARY with offset loop
/// (`00 B1 <hi> <lo>`, offset TLV, short chunks until SW 9000).
/// Returns None when the file does not exist (6A82).
fn read_file_full(card: &pcsc::Card, hi: u8, lo: u8) -> Result<Option<Vec<u8>>, DeviceError> {
    let mut raw: Vec<u8> = Vec::new();
    let mut offset: usize = 0;
    loop {
        let off = (offset & 0xFFFF) as u16;
        let apdu = vec![
            0x00, 0xB1, hi, lo, 0x04, 0x54, 0x02,
            (off >> 8) as u8, (off & 0xFF) as u8, 0x00,
        ];
        let rsp = transmit_raw(card, &apdu)?;
        if rsp.sw_hex == "6A82" {
            return Ok(None);
        }
        if rsp.sw_hex != "9000" && rsp.sw_hex != "6282" {
            return Err(DeviceError::new(
                "ReadFailed",
                format!("READ BINARY {hi:02X}{lo:02X} failed with SW={}", rsp.sw_hex),
                "Retry; if persistent, capture the trace for a bug report.",
            ));
        }
        let chunk = hex::decode(&rsp.data_hex).unwrap_or_default();
        if chunk.is_empty() {
            break;
        }
        raw.extend_from_slice(&chunk);
        offset += chunk.len();
        if raw.len() > 8192 {
            return Err(DeviceError::new(
                "ReadFailed",
                format!("File {hi:02X}{lo:02X} exceeds 8 KiB without terminating"),
                "Capture the trace for a bug report.",
            ));
        }
        if rsp.sw_hex == "9000" && chunk.len() < 256 {
            break;
        }
    }
    // An empty file carries no content — report as absent (e.g. no cert).
    if raw.is_empty() {
        return Ok(None);
    }
    Ok(Some(raw))
}

/// BER-TLV length at `pos`: returns (length, header_len).
fn tlv_len(raw: &[u8], pos: usize) -> Option<(usize, usize)> {
    let first = *raw.get(pos)?;
    if first < 0x80 {
        Some((first as usize, 1))
    } else if first == 0x81 {
        Some((*raw.get(pos + 1)? as usize, 2))
    } else if first == 0x82 {
        let hi = *raw.get(pos + 1)? as usize;
        let lo = *raw.get(pos + 2)? as usize;
        Some(((hi << 8) | lo, 3))
    } else {
        None
    }
}

/// One parsed TLV: tag, content range, total range (indices into the source).
struct Tlv {
    tag: u32,
    constructed: bool,
    content: std::ops::Range<usize>,
    total: std::ops::Range<usize>,
}

/// Parse a single TLV at `pos` (1-2 byte tags, short/81/82 lengths).
fn parse_tlv(raw: &[u8], pos: usize) -> Option<Tlv> {
    let b0 = *raw.get(pos)?;
    let (tag, hdr_tag_len) = if b0 & 0x1F == 0x1F {
        let mut tag = b0 as u32;
        let mut q = pos + 1;
        loop {
            let b = *raw.get(q)?;
            tag = (tag << 8) | b as u32;
            q += 1;
            if b & 0x80 == 0 {
                break;
            }
            if q - pos > 3 {
                return None;
            }
        }
        (tag, q - pos)
    } else {
        (b0 as u32, 1)
    };
    let (len, hdr_len_len) = tlv_len(raw, pos + hdr_tag_len)?;
    let content_start = pos + hdr_tag_len + hdr_len_len;
    let content_end = content_start.checked_add(len)?;
    if content_end > raw.len() {
        return None;
    }
    Some(Tlv {
        tag,
        constructed: b0 & 0x20 != 0,
        content: content_start..content_end,
        total: pos..content_end,
    })
}

/// Minimal-length BER length encoding (short / 81 / 82).
fn encode_len(len: usize) -> Option<Vec<u8>> {
    if len < 128 {
        Some(vec![len as u8])
    } else if len < 256 {
        Some(vec![0x81, len as u8])
    } else if len < 65536 {
        Some(vec![0x82, (len >> 8) as u8, (len & 0xFF) as u8])
    } else {
        None
    }
}

/// Children ranges of a constructed TLV's content.
fn children(raw: &[u8], content: &std::ops::Range<usize>) -> Option<Vec<Tlv>> {
    let mut out = Vec::new();
    let mut p = content.start;
    while p < content.end {
        let t = parse_tlv(raw, p)?;
        p = t.total.end;
        out.push(t);
    }
    if p != content.end {
        return None;
    }
    Some(out)
}

/// Replace (or insert as first child) the PKCS#15 label:
/// outer SEQUENCE -> first nested SEQUENCE -> primitive [0] (0x80).
/// Lengths are re-encoded minimally, so shorter/longer labels stay valid.
/// Returns None on any structural mismatch (caller reports LabelParse).
/// Location of the label slot inside a description record.
/// Accepted shapes (strictly validated, anything else is rejected):
/// - Layout A (PKCS#15 style): outer SEQUENCE -> first nested SEQUENCE ->
///   primitive context tag [0] (0x80). Absent [0] means insert-as-first.
/// - Layout B/C (firmware meta, observed on AES/EC keys): any constructed
///   outer tag (A8, A0, ...) -> first nested SEQUENCE whose first child is
///   a primitive UTF8String (0x0C), e.g. `A8{ 30{0C 00} ... }`.
///   Sibling fields carry id/size (e.g. OCTET "12", INT 528).
struct LabelSlot {
    outer_tag: u8,
    outer_content: std::ops::Range<usize>,
    inner_content: std::ops::Range<usize>,
    /// Total range of the existing label TLV (None = insert new).
    old_label: Option<std::ops::Range<usize>>,
    /// Tag byte to use for the label TLV (0x80 layout A, 0x0C layout B/C).
    label_tag: u8,
}

fn locate_label(raw: &[u8]) -> Option<LabelSlot> {
    let outer = parse_tlv(raw, 0)?;
    if !outer.constructed || outer.total.start != 0 {
        return None;
    }
    // Single-byte outer tag only (0x30, 0xA8, 0xA0, ...); the inner-shape
    // check below is the strict part, the tag just selects rebuild form.
    if outer.content.start - outer.total.start < 2
        || outer.content.start - outer.total.start > 4
        || raw[outer.total.start] & 0x1F == 0x1F
    {
        return None;
    }
    let outer_tag = raw[outer.total.start];
    let outer_kids = children(raw, &outer.content)?;
    let inner = outer_kids.first().filter(|t| t.tag == 0x30 && t.constructed)?;
    let inner_kids = children(raw, &inner.content)?;
    let base = LabelSlot {
        outer_tag,
        outer_content: outer.content.clone(),
        inner_content: inner.content.clone(),
        old_label: None,
        label_tag: 0x80,
    };
    // Layout A: primitive [0] anywhere among the inner children.
    if let Some(found) = inner_kids.iter().find(|t| t.tag == 0x80 && !t.constructed) {
        return Some(LabelSlot { old_label: Some(found.total.clone()), ..base });
    }
    // Layout B/C: first inner child is a primitive UTF8String.
    if let Some(first) = inner_kids.first() {
        if first.tag == 0x0C && !first.constructed {
            return Some(LabelSlot {
                old_label: Some(first.total.clone()),
                label_tag: 0x0C,
                ..base
            });
        }
    }
    // Layout A without a label yet: insert as first inner child.
    // (Only for the classic SEQUENCE outer; meta layouts always carry the slot.)
    if outer_tag == 0x30 {
        return Some(base);
    }
    None
}

fn patch_label(raw: &[u8], label: &str) -> Option<Vec<u8>> {
    let slot = locate_label(raw)?;
    let label_bytes = label.as_bytes();
    let mut label_tlv = vec![slot.label_tag];
    label_tlv.extend_from_slice(&encode_len(label_bytes.len())?);
    label_tlv.extend_from_slice(label_bytes);
    // Rebuild inner content: new label first, old label TLV dropped if present.
    // (Inner children order is rebuilt identically otherwise.)
    let inner_kids = children(raw, &slot.inner_content)?;
    let mut inner_content = label_tlv;
    // Byte offset of the inner content start, to compare positions.
    for kid in &inner_kids {
        if let Some(ref old) = slot.old_label {
            if kid.total == *old {
                continue;
            }
        }
        inner_content.extend_from_slice(&raw[kid.total.clone()]);
    }
    let mut inner_tlv = vec![0x30];
    inner_tlv.extend_from_slice(&encode_len(inner_content.len())?);
    inner_tlv.extend_from_slice(&inner_content);
    // Rebuild outer content: patched inner first, rest untouched.
    let outer_kids = children(raw, &slot.outer_content)?;
    let mut outer_content = inner_tlv;
    for kid in outer_kids.iter().skip(1) {
        outer_content.extend_from_slice(&raw[kid.total.clone()]);
    }
    // Outer tag byte stays untouched (0x30, 0xA8, 0xA0, ...), length re-encoded.
    let mut out = vec![slot.outer_tag];
    out.extend_from_slice(&encode_len(outer_content.len())?);
    out.extend_from_slice(&outer_content);
    Some(out)
}

/// Set the label of a description record (PRKD C4 / CD C8 / DCOD C9):
/// read, patch label TLV, full UPDATE EF at offset 0.
/// Needs User-PIN authentication (same-connection VERIFY when `pin` is given).
/// Only existing records are patched — no fabricated PRKD content.
#[tauri::command]
pub fn set_label(reader: String, fid_hex: String, label: String, pin: Option<String>) -> Result<String, DeviceError> {
    let fid = fid_hex.trim().replace([' ', ':'], "");
    if fid.len() != 4 || !fid.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err(DeviceError::new(
            "BadFid",
            "FID must be 4 hex chars (e.g. C401)".to_string(),
            "This is a frontend bug — please report.",
        ));
    }
    let hi = u8::from_str_radix(&fid[0..2], 16).unwrap_or(0);
    let lo = u8::from_str_radix(&fid[2..4], 16).unwrap_or(0);
    if !matches!(hi, 0xC4 | 0xC8 | 0xC9) {
        return Err(DeviceError::new(
            "BadFid",
            "Only description records (C4/C8/C9) carry editable labels".to_string(),
            "Keys (CC) and certificates (CE) inherit their label from the description record.",
        ));
    }
    validate_label(&label)?;
    with_card(&reader, |card| {
        select_hsm(card)?;
        if let Some(ref p) = pin {
            verify_pin(card, p)?;
        }
        write_label_record(card, hi, lo, &fid, &label)?;
        Ok(format!("Label of {fid} set to “{label}”."))
    })
}

/// Validate a label once (shared by set_label and key generation).
fn validate_label(label: &str) -> Result<(), DeviceError> {
    if label.is_empty() || label.len() > 64 || label.chars().any(|c| c.is_control()) {
        return Err(DeviceError::new(
            "BadLabel",
            "Label must be 1-64 chars without control characters".to_string(),
            "Keep it short and readable.",
        ));
    }
    Ok(())
}

/// Patch + write the label of an existing description record.
/// Shared by set_label (strict) and key generation (best-effort via caller).
fn write_label_record(card: &pcsc::Card, hi: u8, lo: u8, fid: &str, label: &str) -> Result<(), DeviceError> {
    let current = read_file_full(card, hi, lo)?.ok_or_else(|| {
        DeviceError::new(
            "LabelMissing",
            format!("No description record {fid} on the device"),
            "Only existing records can be renamed in this version.",
        )
    })?;
    let patched = patch_label(&current, label).ok_or_else(|| {
        DeviceError::new(
            "LabelParse",
            format!("Description record {fid} has an unexpected layout"),
            "Post the Diagnose details for analysis — nothing was written.",
        )
    })?;
    // Full replace at offset 0: [54 02 0000] + [53 len data], short APDU only.
    let mut data = vec![0x54, 0x02, 0x00, 0x00, 0x53];
    let len_enc = encode_len(patched.len()).ok_or_else(|| {
        DeviceError::new("LabelParse", "Patched record too large".to_string(), "Use a shorter label.")
    })?;
    data.extend_from_slice(&len_enc);
    data.extend_from_slice(&patched);
    if data.len() > 255 {
        return Err(DeviceError::new(
            "LabelParse",
            "Patched record exceeds short APDU".to_string(),
            "Use a shorter label.",
        ));
    }
    let mut apdu = vec![0x00, 0xD7, hi, lo, data.len() as u8];
    apdu.extend_from_slice(&data);
    apdu.push(0x00);
    let rsp = transmit_on_card(card, &apdu)?;
    if rsp.sw_hex == "6982" {
        return Err(DeviceError::auth_required(
            "LabelAuth",
            "Label write needs User-PIN authentication (SW=6982)".to_string(),
        ));
    }
    if rsp.sw_hex != "9000" {
        return Err(DeviceError::new(
            "LabelWriteFailed",
            format!("Label write failed with SW={}", rsp.sw_hex),
            "Nothing was changed. Retry; if persistent, capture the trace.",
        ));
    }
    Ok(())
}

/// Extract the label via [`locate_label`]: primitive [0] (0x80, layout A)
/// or leading UTF8String (0x0C, layout B firmware meta).
/// Returns None when absent (caller shows N/A) — never an error.
fn parse_label(raw: &[u8]) -> Option<String> {
    let slot = locate_label(raw)?;
    let old = slot.old_label?;
    // Value = old TLV bytes minus tag+length header (re-parse for exactness).
    let t = parse_tlv(raw, old.start)?;
    let s = String::from_utf8_lossy(&raw[t.content.clone()]).into_owned();
    if s.is_empty() {
        return None;
    }
    Some(s)
}

/// Enumerate on-device objects (`80 58 00 00 00`, no auth).
/// Returns FID pairs (device key id 0 included when present).
#[tauri::command]
pub fn list_keys(reader: String) -> Result<Vec<KeyEntry>, DeviceError> {
    with_card(&reader, |card| {
        select_hsm(card)?;
        let rsp = transmit_on_card(card, &[0x80, 0x58, 0x00, 0x00, 0x00])?;
        if rsp.sw_hex != "9000" {
            return Err(DeviceError::new(
                "ListFailed",
                format!("ENUMERATE OBJECTS failed with SW={}", rsp.sw_hex),
                "Check firmware version (>= 5.x expected for INS 0x58).",
            ));
        }
        let raw = hex::decode(&rsp.data_hex).unwrap_or_default();
        if raw.len() % 2 != 0 {
            return Err(DeviceError::new(
                "ListParse",
                format!("Expected even-length FID list, got {} byte(s)", raw.len()),
                "Capture the trace for a bug report.",
            ));
        }
        let mut out = Vec::new();
        for pair in raw.chunks_exact(2) {
            let (hi, lo) = (pair[0], pair[1]);
            // 0000 is the firmware's 64-byte-alignment padding, not a file.
            if hi == 0x00 && lo == 0x00 {
                continue;
            }
            let kind = classify_fid(hi);
            // Labels + raw description bytes, best-effort (None -> N/A).
            let (label, desc_hex) = match hi {
                0xC4 | 0xC8 | 0xC9 => match read_file_full(card, hi, lo) {
                    Ok(Some(bytes)) => (parse_label(&bytes), Some(hex::encode(&bytes).to_uppercase())),
                    _ => (None, None),
                },
                _ => (None, None),
            };
            out.push(KeyEntry {
                fid: format!("{hi:02X}{lo:02X}"),
                kind: kind.to_string(),
                id: lo,
                label,
                desc_hex,
                raw_hex: rsp.data_hex.clone(),
            });
        }
        // EE certs (CE) carry no own description: inherit the same-id PRKD label.
        // Key id 0 is the device key.
        for i in 0..out.len() {
            if out[i].fid.starts_with("CE") {
                let id = out[i].id;
                let inherited = out
                    .iter()
                    .find(|e| e.fid == format!("C4{id:02X}"))
                    .map(|prkd| (prkd.label.clone(), prkd.desc_hex.clone()));
                if let Some((label, desc_hex)) = inherited {
                    out[i].label = label;
                    out[i].desc_hex = desc_hex;
                }
            } else if out[i].id == 0 && out[i].label.is_none() {
                out[i].label = Some("Device key".to_string());
            }
        }
        Ok(out)
    })
}

#[derive(Debug, Serialize, Clone)]
pub struct GenResult {
    pub id: u8,
    pub kind: String,
    pub detail: String,
    /// True when the requested label was written to the PRKD record.
    pub label_written: bool,
    /// Human-readable outcome incl. label note.
    pub message: String,
    /// SPKI DER (hex) captured from the GAK response, when parseable.
    /// Enables CSR for keys without a stored certificate. Public key only.
    pub spki_hex: Option<String>,
}

/// Recursive depth-first TLV search (max depth 6).
fn find_tag(raw: &[u8], range: &std::ops::Range<usize>, tag: u32, depth: u8) -> Option<Tlv> {
    if depth > 6 {
        return None;
    }
    for kid in children(raw, range)? {
        if kid.tag == tag {
            return Some(kid);
        }
        if kid.constructed {
            if let Some(found) = find_tag(raw, &kid.content, tag, depth + 1) {
                return Some(found);
            }
        }
    }
    None
}

/// Trailing size INTEGER of a description record: outer children -> A1/A0
/// block -> first `02 02`. Observed: RSA 1024/2048/..., AES 128-512,
/// EC byte-aligned field bits (e.g. 528 for secp521r1).
fn trailing_size(raw: &[u8]) -> Option<u32> {
    let outer = parse_tlv(raw, 0)?;
    if !outer.constructed {
        return None;
    }
    let kids = children(raw, &outer.content)?;
    for kid in &kids {
        if (kid.tag == 0xA1 || kid.tag == 0xA0) && kid.constructed {
            if let Some(t) = find_tag(raw, &kid.content, 0x02, 0) {
                let b = raw.get(t.content.clone())?;
                if b.len() == 2 {
                    return Some(((b[0] as u32) << 8) | b[1] as u32);
                }
            }
        }
    }
    None
}

/// Match CVC prime bytes against the 13 supported curves.
fn curve_name_by_prime(prime: &[u8]) -> Option<&'static str> {
    const CURVES: [&str; 13] = [
        "secp192r1", "secp256r1", "secp384r1", "secp521r1", "brainpoolP256r1", "brainpoolP384r1",
        "brainpoolP512r1", "secp192k1", "secp256k1", "curve25519", "curve448", "ed25519", "ed448",
    ];
    for name in CURVES {
        if let Some((_, dom)) = ec_domain(name) {
            if let Ok(p) = hex::decode(dom.p) {
                if p == prime {
                    return Some(name);
                }
            }
        }
    }
    None
}

/// Nominal bits per curve (field size; X/Ed by key size convention).
fn curve_bits(name: &str) -> Option<u32> {
    match name {
        "secp192r1" | "secp192k1" => Some(192),
        "secp256r1" | "secp256k1" | "brainpoolP256r1" | "ed25519" => Some(256),
        "curve25519" => Some(255),
        "secp384r1" | "brainpoolP384r1" => Some(384),
        "curve448" | "ed448" => Some(448),
        "secp521r1" => Some(521),
        "brainpoolP512r1" => Some(512),
        _ => None,
    }
}

#[derive(Debug, Serialize, Clone)]
pub struct KeyDetails {
    pub id: u8,
    pub label: Option<String>,
    pub files: Vec<String>,
    /// RSA | EC | AES | unknown.
    pub key_type: String,
    /// cvc (EE-cert OID parse) | heuristic (size sets) | none.
    pub type_source: String,
    pub size_bits: Option<u32>,
    pub curve: Option<String>,
    /// Static PKCS#15 usage word from the description BIT STRING
    /// (NOT the configured GAK-0x91 restrictions).
    pub usage: Option<u32>,
}

/// Semantic key details: type from the EE-cert CVC pubkey OID
/// (authoritative), size from the trailing record INTEGER, curve by
/// prime match. Counter/purposes are NOT exposed by the firmware
/// (frontend shows N/A with reason).
#[tauri::command]
pub fn key_details(reader: String, id: u8) -> Result<KeyDetails, DeviceError> {
    with_card(&reader, |card| {
        select_hsm(card)?;
        // FIDs present for this id.
        let rsp = transmit_on_card(card, &[0x80, 0x58, 0x00, 0x00, 0x00])?;
        if rsp.sw_hex != "9000" {
            return Err(DeviceError::new(
                "ListFailed",
                format!("ENUMERATE OBJECTS failed with SW={}", rsp.sw_hex),
                "Retry; if persistent, capture the trace.",
            ));
        }
        let raw = hex::decode(&rsp.data_hex).unwrap_or_default();
        let mut files: Vec<String> = raw
            .chunks_exact(2)
            .filter(|p| !(p[0] == 0x00 && p[1] == 0x00) && p[1] == id)
            .map(|p| format!("{:02X}{:02X}", p[0], p[1]))
            .collect();
        files.sort();
        files.dedup();
        if files.is_empty() {
            return Err(DeviceError::new(
                "NotFound",
                format!("No objects for ID {id} on the device"),
                "The key may have been deleted — reload the list.",
            ));
        }
        // Description record (C4 preferred) for label + size.
        let mut c4: Option<Vec<u8>> = None;
        for prefix in [0xC4, 0xC8, 0xC9] {
            if files.iter().any(|f| f == &format!("{prefix:02X}{id:02X}")) {
                if let Ok(Some(bytes)) = read_file_full(card, prefix, id) {
                    c4 = Some(bytes);
                    break;
                }
            }
        }
        let label = c4.as_ref().and_then(|b| parse_label(b));
        let size_int = c4.as_ref().and_then(|b| trailing_size(b));
        // EE cert decides asymmetric vs symmetric + curve.
        // type_source tracks HOW the type was derived: cvc (authoritative
        // CVC-OID parse) or heuristic (ambiguous size sets).
        // NOTE: deliberately OID-only here (not parse_cvc_pubkey): details
        // needs type+curve, never key material, plus heuristic fallbacks.
        let mut key_type = "unknown".to_string();
        let mut type_source = "none".to_string();
        let mut size_bits: Option<u32> = None;
        let mut curve: Option<String> = None;
        if files.iter().any(|f| f.starts_with("CE")) {
            if let Ok(Some(cert)) = read_file_full(card, 0xCE, id) {
                if let Some(t7949) = find_tag(&cert, &(0..cert.len()), 0x7F49, 0) {
                    if let Some(oid_t) = find_tag(&cert, &t7949.content, 0x06, 1) {
                        let oid = &cert[oid_t.content.clone()];
                        if oid == OID_RSA_V15_SHA256 {
                            key_type = "RSA".to_string();
                            type_source = "cvc".to_string();
                            size_bits = size_int;
                        } else if oid == OID_ECDSA_SHA256 {
                            key_type = "EC".to_string();
                            type_source = "cvc".to_string();
                            if let Some(p) = find_tag(&cert, &t7949.content, 0x81, 1) {
                                if let Some(name) = curve_name_by_prime(&cert[p.content.clone()]) {
                                    size_bits = curve_bits(name);
                                    curve = Some(name.to_string());
                                }
                            }
                            if size_bits.is_none() {
                                size_bits = size_int;
                            }
                        }
                    }
                }
            }
            if key_type == "unknown" {
                // CE present but unparsable: fall back to size sets below.
                if let Some(n) = size_int {
                    if [1024, 2048, 3072, 4096].contains(&n) {
                        key_type = "RSA".to_string();
                        type_source = "heuristic".to_string();
                        size_bits = Some(n);
                    }
                }
            }
        } else if let Some(n) = size_int {
            if [128, 192, 256, 512].contains(&n) {
                key_type = "AES".to_string();
                type_source = "heuristic".to_string();
                size_bits = Some(n);
            } else if [1024, 2048, 3072, 4096].contains(&n) {
                key_type = "RSA".to_string();
                type_source = "heuristic".to_string();
                size_bits = Some(n);
            }
        }
        Ok(KeyDetails {
            id,
            label,
            files,
            key_type,
            type_source,
            size_bits,
            curve,
            usage: c4.as_ref().and_then(|b| parse_usage(b)),
        })
    })
}

/// First free key id (1-255) from the on-device FID list. ID 0 is the device key.
fn first_free_id(card: &pcsc::Card) -> Result<u8, DeviceError> {
    let rsp = transmit_on_card(card, &[0x80, 0x58, 0x00, 0x00, 0x00])?;
    if rsp.sw_hex != "9000" {
        return Err(DeviceError::new(
            "ListFailed",
            format!("ENUMERATE OBJECTS failed with SW={}", rsp.sw_hex),
            "Check firmware version (>= 5.x expected for INS 0x58).",
        ));
    }
    let raw = hex::decode(&rsp.data_hex).unwrap_or_default();
    let mut used = [false; 256];
    for pair in raw.chunks_exact(2) {
        if pair[0] == 0xCC || pair[0] == 0xC4 || pair[0] == 0xCE {
            used[pair[1] as usize] = true;
        }
    }
    (1u8..=255).find(|i| !used[*i as usize]).ok_or_else(|| {
        DeviceError::new(
            "NoFreeId",
            "No free key id (1-255) left on the device".to_string(),
            "Delete an unused key first.",
        )
    })
}

/// Extract the SPKI (hex) from a GAK response, best-effort.
/// The response carries the fresh public key as a 7F49 template (same shape
/// as CVC pubkeys). None when absent/unparsable — generation still succeeds;
/// CSR then needs a stored certificate instead.
fn gak_spki_hex(rsp: &TransmitResult) -> Option<String> {
    let raw = hex::decode(&rsp.data_hex).ok()?;
    let pk = parse_cvc_pubkey(&raw)?;
    spki_from_cvc(&pk).ok().map(|spki| hex::encode(spki).to_uppercase())
}

/// Map a key-generation response. 6982 -> auth (should not happen after VERIFY).
fn map_gen_result(rsp: &TransmitResult, what: &str, id: u8) -> Result<(), DeviceError> {
    if rsp.sw_hex == "9000" {
        return Ok(());
    }
    if rsp.sw_hex == "6982" {
        return Err(DeviceError::auth_required(
            "GenAuth",
            format!("{what} needs User-PIN authentication (SW=6982)"),
        ));
    }
    Err(DeviceError::new(
        "GenFailed",
        format!("{what} failed with SW={} (id {id})", rsp.sw_hex),
        "Retry; if persistent, capture the trace for a bug report.",
    ))
}

/// Best-effort label write after generation: patch C4<id> when the record
/// exists, otherwise report that no label record was created (spike data
/// for the PRKD-coverage question). Generation itself stays successful.
fn label_after_gen(card: &pcsc::Card, id: u8, label: &str) -> (bool, String) {
    match read_file_full(card, 0xC4, id) {
        Ok(Some(current)) => match patch_label(&current, label) {
            Some(patched) => {
                let mut data = vec![0x54, 0x02, 0x00, 0x00, 0x53];
                match encode_len(patched.len()) {
                    Some(le) => {
                        data.extend_from_slice(&le);
                        data.extend_from_slice(&patched);
                    }
                    None => {
                        return (
                            false,
                            "Key generated, but the label is too long to store.".to_string(),
                        )
                    }
                }
                if data.len() > 255 {
                    return (
                        false,
                        "Key generated, but the label record exceeds short APDU.".to_string(),
                    );
                }
                let mut apdu = vec![0x00, 0xD7, 0xC4, id, data.len() as u8];
                apdu.extend_from_slice(&data);
                apdu.push(0x00);
                match transmit_on_card(card, &apdu) {
                    Ok(rsp) if rsp.sw_hex == "9000" => (true, "Label written.".to_string()),
                    Ok(rsp) => (
                        false,
                        format!("Key generated, but label write failed (SW={}).", rsp.sw_hex),
                    ),
                    Err(e) => (false, format!("Key generated, but label write failed: {}.", e.message)),
                }
            }
            None => (
                false,
                "Key generated, but the PRKD layout is unexpected (label not written).".to_string(),
            ),
        },
        _ => (
            false,
            "Key generated, but no PRKD record exists yet (label not written).".to_string(),
        ),
    }
}

/// Generate an AES key (`00 48 <id> <B0..B3>`, auth required).
/// Sizes: 128/192/256/512 bits (512 = XTS double key).
#[tauri::command]
pub fn gen_aes(
    reader: String,
    bits: u16,
    label: Option<String>,
    pin: Option<String>,
) -> Result<GenResult, DeviceError> {
    let p2 = match bits {
        128 => 0xB0,
        192 => 0xB1,
        256 => 0xB2,
        512 => 0xB3,
        _ => {
            return Err(DeviceError::new(
                "BadSize",
                "AES size must be 128, 192, 256 or 512 bits".to_string(),
                "This is a frontend bug — please report.",
            ))
        }
    };
    if let Some(ref l) = label {
        validate_label(l)?;
    }
    with_card(&reader, |card| {
        select_hsm(card)?;
        if let Some(ref p) = pin {
            verify_pin(card, p).map_err(|e| e.at_step("VERIFY"))?;
        }
        let id = first_free_id(card).map_err(|e| e.at_step("LIST"))?;
        let rsp = transmit_on_card(card, &[0x00, 0x48, id, p2, 0x00]).map_err(|e| e.at_step("GENERATE"))?;
        map_gen_result(&rsp, &format!("AES-{bits} generation"), id)?;
        let mut message = format!("AES-{bits} generated as ID {id}.");
        let mut label_written = false;
        if let Some(l) = label {
            let (ok, note) = label_after_gen(card, id, &l);
            label_written = ok;
            message.push(' ');
            message.push_str(&note);
        }
        Ok(GenResult {
            id,
            kind: "aes".to_string(),
            detail: format!("AES-{bits}"),
            label_written,
            message,
            spki_hex: None,
        })
    })
}

/// GAK TLV building blocks (verified against firmware oid.h + pypicohsm).
const OID_RSA_V15_SHA256: [u8; 10] =
    [0x04, 0x00, 0x7F, 0x00, 0x07, 0x02, 0x02, 0x02, 0x01, 0x02];
const OID_ECDSA_SHA256: [u8; 10] =
    [0x04, 0x00, 0x7F, 0x00, 0x07, 0x02, 0x02, 0x02, 0x02, 0x03];

fn tlv(tag: u8, value: &[u8]) -> Option<Vec<u8>> {
    let mut out = vec![tag];
    out.extend_from_slice(&encode_len(value.len())?);
    out.extend_from_slice(value);
    Some(out)
}

/// Case-4 APDU, short or extended form depending on data length
/// (secp521r1 GAK exceeds 255 bytes and needs extended form).
fn case4(cla: u8, ins: u8, p1: u8, p2: u8, data: &[u8]) -> Option<Vec<u8>> {
    let mut apdu = vec![cla, ins, p1, p2];
    if data.len() <= 255 {
        apdu.push(data.len() as u8);
        apdu.extend_from_slice(data);
        apdu.push(0x00);
    } else if data.len() <= 65535 {
        apdu.push(0x00);
        apdu.push((data.len() >> 8) as u8);
        apdu.push((data.len() & 0xFF) as u8);
        apdu.extend_from_slice(data);
        apdu.push(0x00);
        apdu.push(0x00);
    } else {
        return None;
    }
    Some(apdu)
}

/// GAK request for RSA: 7F49{06 OID_RSA, 02 bits BE} + CVC context tags.
fn gak_rsa(bits: u16) -> Option<Vec<u8>> {
    let mut inner = vec![0x06];
    inner.extend_from_slice(&encode_len(OID_RSA_V15_SHA256.len())?);
    inner.extend_from_slice(&OID_RSA_V15_SHA256);
    inner.extend_from_slice(&tlv(0x02, &bits.to_be_bytes())?);
    gak_wrap(&inner)
}

/// Shared GAK envelope: 5F29 + 42 "UTCA00001" + 7F49{...} + 5F20 "UTCDUMMY00001".
fn gak_wrap(inner_7f49_content: &[u8]) -> Option<Vec<u8>> {
    let mut outer = vec![0x5F, 0x29, 0x01, 0x00];
    outer.extend_from_slice(b"\x42\x09UTCA00001");
    outer.push(0x7F);
    outer.push(0x49);
    outer.extend_from_slice(&encode_len(inner_7f49_content.len())?);
    outer.extend_from_slice(inner_7f49_content);
    outer.extend_from_slice(b"\x5F\x20\x0DUTCDUMMY00001");
    Some(outer)
}

/// GAK meta suffix (top-level siblings after the CVC object, like pypicohsm
/// and OpenSC build them): `90 04 <counter BE>` and `91 <len> <algos>`.
/// `allowed` restricts algorithm bytes per key type; empty means unrestricted.
fn gak_meta(
    base: Vec<u8>,
    use_counter: Option<u32>,
    algorithms: Option<Vec<u8>>,
    allowed: &[u8],
    what: &str,
) -> Result<Vec<u8>, DeviceError> {
    let mut gak = base;
    if let Some(n) = use_counter {
        if n == 0 {
            return Err(DeviceError::new(
                "BadCounter",
                "Usage limit must be at least 1 (or Unlimited)".to_string(),
                "This is a frontend bug — please report.",
            ));
        }
        gak.extend_from_slice(&[0x90, 0x04]);
        gak.extend_from_slice(&n.to_be_bytes());
    }
    if let Some(algos) = algorithms {
        if algos.is_empty() || algos.len() > 16 || algos.iter().any(|a| !allowed.contains(a)) {
            return Err(DeviceError::new(
                "BadPurpose",
                format!("Algorithm list invalid for {what}"),
                "This is a frontend bug — please report.",
            ));
        }
        gak.push(0x91);
        gak.push(algos.len() as u8);
        gak.extend_from_slice(&algos);
    }
    Ok(gak)
}

/// Allowed 0x91 algorithm bytes per key type (subset of sc_hsm.h ALGO_*).
const RSA_ALGOS: [u8; 6] = [0x33, 0x43, 0x22, 0x23, 0x92, 0x93];
const EC_ALGOS: [u8; 4] = [0x73, 0x80, 0x92, 0x93];

/// Generate an RSA keypair (`00 46 <id> 00` + GAK, auth required).
/// Sizes 1024-4096 bits. WARNING: 2048 takes ~2 min, 4096 ~17 min
/// with the device blocked meanwhile — the frontend shows a busy state.
#[tauri::command]
pub fn gen_rsa(
    reader: String,
    bits: u16,
    label: Option<String>,
    pin: Option<String>,
    use_counter: Option<u32>,
    algorithms: Option<Vec<u8>>,
) -> Result<GenResult, DeviceError> {
    if ![1024, 2048, 3072, 4096].contains(&bits) {
        return Err(DeviceError::new(
            "BadSize",
            "RSA size must be 1024, 2048, 3072 or 4096 bits".to_string(),
            "This is a frontend bug — please report.",
        ));
    }
    if let Some(ref l) = label {
        validate_label(l)?;
    }
    let gak = gak_rsa(bits).ok_or_else(|| {
        DeviceError::new("GenFailed", "Failed to build GAK request".to_string(), "Please report this bug.")
    })?;
    let gak = gak_meta(gak, use_counter, algorithms, &RSA_ALGOS, "RSA")?;
    if gak.len() > 255 {
        return Err(DeviceError::new(
            "GenFailed",
            "GAK request exceeds short APDU".to_string(),
            "Please report this bug.",
        ));
    }
    with_card(&reader, |card| {
        select_hsm(card)?;
        if let Some(ref p) = pin {
            verify_pin(card, p).map_err(|e| e.at_step("VERIFY"))?;
        }
        let id = first_free_id(card).map_err(|e| e.at_step("LIST"))?;
        let mut apdu = vec![0x00, 0x46, id, 0x00, gak.len() as u8];
        apdu.extend_from_slice(&gak);
        apdu.push(0x00);
        // NOTE: RSA-3072/4096 block the device for many minutes; the
        // global PCSC lock serializes everything meanwhile by design.
        let rsp = transmit_on_card(card, &apdu).map_err(|e| e.at_step("GENERATE"))?;
        map_gen_result(&rsp, &format!("RSA-{bits} generation"), id)?;
        let mut message = format!("RSA-{bits} generated as ID {id}.");
        let mut label_written = false;
        if let Some(l) = label {
            let (ok, note) = label_after_gen(card, id, &l);
            label_written = ok;
            message.push(' ');
            message.push_str(&note);
        }
        Ok(GenResult {
            id,
            kind: "rsa".to_string(),
            detail: format!("RSA-{bits}"),
            label_written,
            message,
            spki_hex: gak_spki_hex(&rsp),
        })
    })
}

/// EC domain parameters as hex (P, A, B, G, O, F), transcribed from
/// pycvc ec_curves.py (same source pypicohsm uses). Only curves from the
/// tool's allow-list are exposed.
struct EcDomain {
    p: &'static str,
    a: &'static str,
    b: &'static str,
    g: &'static str,
    o: &'static str,
    f: &'static str,
}

/// (curve name, short-domain-only[P,O,G], domain). Short form is used for
/// curve25519/curve448/ed25519/ed448, full form otherwise.
fn ec_domain(curve: &str) -> Option<(bool, EcDomain)> {
    let short = |p: &'static str, o: &'static str, g: &'static str| {
        (
            true,
            EcDomain { p, a: "", b: "", g, o, f: "" },
        )
    };
    let full = |p: &'static str, a: &'static str, b: &'static str, g: &'static str, o: &'static str, f: &'static str| {
        (
            false,
            EcDomain { p, a, b, g, o, f },
        )
    };
    match curve {
        "secp192r1" => Some(full(
            "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFFFFFFFFFFFF",
            "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFFFFFFFFFFFC",
            "64210519E59C80E70FA7E9AB72243049FEB8DEECC146B9B1",
            "04188DA80EB03090F67CBF20EB43A18800F4FF0AFD82FF101207192B95FFC8DA78631011ED6B24CDD573F977A11E794811",
            "FFFFFFFFFFFFFFFFFFFFFFFF99DEF836146BC9B1B4D22831",
            "01",
        )),
        "secp256r1" => Some(full(
            "FFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF",
            "FFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFC",
            "5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B",
            "046B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C2964FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5",
            "FFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551",
            "01",
        )),
        "secp384r1" => Some(full(
            "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFFFF0000000000000000FFFFFFFF",
            "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFFFF0000000000000000FFFFFFFC",
            "B3312FA7E23EE7E4988E056BE3F82D19181D9C6EFE8141120314088F5013875AC656398D8A2ED19D2A85C8EDD3EC2AEF",
            "04AA87CA22BE8B05378EB1C71EF320AD746E1D3B628BA79B9859F741E082542A385502F25DBF55296C3A545E3872760AB73617DE4A96262C6F5D9E98BF9292DC29F8F41DBD289A147CE9DA3113B5F0B8C00A60B1CE1D7E819D7A431D7C90EA0E5F",
            "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFC7634D81F4372DDF581A0DB248B0A77AECEC196ACCC52973",
            "01",
        )),
        "secp521r1" => Some(full(
            "01FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF",
            "01FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFC",
            "0051953EB9618E1C9A1F929A21A0B68540EEA2DA725B99B315F3B8B489918EF109E156193951EC7E937B1652C0BD3BB1BF073573DF883D2C34F1EF451FD46B503F00",
            "0400C6858E06B70404E9CD9E3ECB662395B4429C648139053FB521F828AF606B4D3DBAA14B5E77EFE75928FE1DC127A2FFA8DE3348B3C1856A429BF97E7E31C2E5BD66011839296A789A3BC0045C8A5FB42C7D1BD998F54449579B446817AFBD17273E662C97EE72995EF42640C550B9013FAD0761353C7086A272C24088BE94769FD16650",
            "01FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFA51868783BF2F966B7FCC0148F709A5D03BB5C9B8899C47AEBB6FB71E91386409",
            "01",
        )),
        "brainpoolP256r1" => Some(full(
            "A9FB57DBA1EEA9BC3E660A909D838D726E3BF623D52620282013481D1F6E5377",
            "7D5A0975FC2C3057EEF67530417AFFE7FB8055C126DC5C6CE94A4B44F330B5D9",
            "26DC5C6CE94A4B44F330B5D9BBD77CBF958416295CF7E1CE6BCCDC18FF8C07B6",
            "048BD2AEB9CB7E57CB2C4B482FFC81B7AFB9DE27E1E3BD23C23A4453BD9ACE3262547EF835C3DAC4FD97F8461A14611DC9C27745132DED8E545C1D54C72F046997",
            "A9FB57DBA1EEA9BC3E660A909D838D718C397AA3B561A6F7901E0E82974856A7",
            "01",
        )),
        "brainpoolP384r1" => Some(full(
            "8CB91E82A3386D280F5D6F7E50E641DF152F7109ED5456B412B1DA197FB71123ACD3A729901D1A71874700133107EC53",
            "7BC382C63D8C150C3C72080ACE05AFA0C2BEA28E4FB22787139165EFBA91F90F8AA5814A503AD4EB04A8C7DD22CE2826",
            "04A8C7DD22CE28268B39B55416F0447C2FB77DE107DCD2A62E880EA53EEB62D57CB4390295DBC9943AB78696FA504C11",
            "041D1C64F068CF45FFA2A63A81B7C13F6B8847A3E77EF14FE3DB7FCAFE0CBD10E8E826E03436D646AAEF87B2E247D4AF1E8ABE1D7520F9C2A45CB1EB8E95CFD55262B70B29FEEC5864E19C054FF99129280E4646217791811142820341263C5315",
            "8CB91E82A3386D280F5D6F7E50E641DF152F7109ED5456B31F166E6CAC0425A7CF3AB6AF6B7FC3103B883202E9046565",
            "01",
        )),
        "brainpoolP512r1" => Some(full(
            "AADD9DB8DBE9C48B3FD4E6AE33C9FC07CB308DB3B3C9D20ED6639CCA703308717D4D9B009BC66842AECDA12AE6A380E62881FF2F2D82C68528AA6056583A48F3",
            "7830A3318B603B89E2327145AC234CC594CBDD8D3DF91610A83441CAEA9863BC2DED5D5AA8253AA10A2EF1C98B9AC8B57F1117A72BF2C7B9E7C1AC4D77FC94CA",
            "3DF91610A83441CAEA9863BC2DED5D5AA8253AA10A2EF1C98B9AC8B57F1117A72BF2C7B9E7C1AC4D77FC94CADC083E67984050B75EBAE5DD2809BD638016F723",
            "0481AEE4BDD82ED9645A21322E9C4C6A9385ED9F70B5D916C1B43B62EEF4D0098EFF3B1F78E2D0D48D50D1687B93B97D5F7C6D5047406A5E688B352209BCB9F8227DDE385D566332ECC0EABFA9CF7822FDF209F70024A57B1AA000C55B881F8111B2DCDE494A5F485E5BCA4BD88A2763AED1CA2B2FA8F0540678CD1E0F3AD80892",
            "AADD9DB8DBE9C48B3FD4E6AE33C9FC07CB308DB3B3C9D20ED6639CCA70330870553E5C414CA92619418661197FAC10471DB1D381085DDADDB58796829CA90069",
            "01",
        )),
        "secp192k1" => Some(full(
            "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFEE37",
            "000000000000000000000000000000000000000000000000",
            "000000000000000000000000000000000000000000000003",
            "04DB4FF10EC057E9AE26B07D0280B7F4341DA5D1B1EAE06C7D9B2F2F6D9C5628A7844163D015BE86344082AA88D95E2F9D",
            "FFFFFFFFFFFFFFFFFFFFFFFE26F2FC170F69466A74DEFD8D",
            "01",
        )),
        "secp256k1" => Some(full(
            "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F",
            "0000000000000000000000000000000000000000000000000000000000000000",
            "0000000000000000000000000000000000000000000000000000000000000007",
            "0479BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8",
            "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141",
            "01",
        )),
        "curve25519" => Some(short(
            "7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFED",
            "1000000000000000000000000000000014DEF9DEA2F79CD65812631A5CF5D3ED",
            "0900000000000000000000000000000000000000000000000000000000000000",
        )),
        "curve448" => Some(short(
            "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF",
            "3FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF7CCA23E9C44EDB49AED63690216CC2728DC58F552378C292AB5844F3",
            "0500000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
        )),
        "ed25519" => Some(short(
            "7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFED",
            "1000000000000000000000000000000014DEF9DEA2F79CD65812631A5CF5D3ED",
            "5866666666666666666666666666666666666666666666666666666666666666",
        )),
        "ed448" => Some(short(
            "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF",
            "3FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF7CCA23E9C44EDB49AED63690216CC2728DC58F552378C292AB5844F3",
            "14FA30F25B790898ADC8D74E2C13BDFDC4397CE61CFFD33AD7C2A0051E9C78874098A36C7373EA4B62C7C9563720768824BCB66E71463F6900",
        )),
        _ => None,
    }
}

/// GAK request for ECC: 7F49{06 OID_ECDSA, domain ctx tags} + CVC context.
/// Full domain (81 P, 82 A, 83 B, 84 G, 85 O, 87 F), short form (81 P, 82 O,
/// 83 G) for curve25519/curve448/ed25519/ed448 — mirroring pypicohsm.
fn gak_ec(curve: &str) -> Option<Vec<u8>> {
    let (short_form, dom) = ec_domain(curve)?;
    let decode = |hex: &str| hex::decode(hex).ok();
    let mut inner = vec![0x06];
    inner.extend_from_slice(&encode_len(OID_ECDSA_SHA256.len())?);
    inner.extend_from_slice(&OID_ECDSA_SHA256);
    if short_form {
        inner.extend_from_slice(&tlv(0x81, &decode(dom.p)?)?);
        inner.extend_from_slice(&tlv(0x82, &decode(dom.o)?)?);
        inner.extend_from_slice(&tlv(0x83, &decode(dom.g)?)?);
    } else {
        inner.extend_from_slice(&tlv(0x81, &decode(dom.p)?)?);
        inner.extend_from_slice(&tlv(0x82, &decode(dom.a)?)?);
        inner.extend_from_slice(&tlv(0x83, &decode(dom.b)?)?);
        inner.extend_from_slice(&tlv(0x84, &decode(dom.g)?)?);
        inner.extend_from_slice(&tlv(0x85, &decode(dom.o)?)?);
        inner.extend_from_slice(&tlv(0x87, &decode(dom.f)?)?);
    }
    gak_wrap(&inner)
}

/// Generate an EC keypair (`00 46 <id> 00` + GAK, auth required).
/// Near-instant like AES. secp521r1 GAK exceeds short APDU (extended form).
#[tauri::command]
pub fn gen_ec(
    reader: String,
    curve: String,
    label: Option<String>,
    pin: Option<String>,
    use_counter: Option<u32>,
    algorithms: Option<Vec<u8>>,
) -> Result<GenResult, DeviceError> {
    if let Some(ref l) = label {
        validate_label(l)?;
    }
    let gak = gak_ec(&curve).ok_or_else(|| {
        DeviceError::new(
            "BadCurve",
            format!("Unsupported curve '{curve}'"),
            "This is a frontend bug — please report.",
        )
    })?;
    let gak = gak_meta(gak, use_counter, algorithms, &EC_ALGOS, "EC")?;
    with_card(&reader, |card| {
        select_hsm(card)?;
        if let Some(ref p) = pin {
            verify_pin(card, p).map_err(|e| e.at_step("VERIFY"))?;
        }
        let id = first_free_id(card).map_err(|e| e.at_step("LIST"))?;
        let apdu = case4(0x00, 0x46, id, 0x00, &gak).ok_or_else(|| {
            DeviceError::new("GenFailed", "GAK request too large".to_string(), "Please report this bug.")
        })?;
        let rsp = transmit_on_card(card, &apdu).map_err(|e| e.at_step("GENERATE"))?;
        map_gen_result(&rsp, &format!("EC {curve} generation"), id)?;
        let mut message = format!("EC {curve} generated as ID {id}.");
        let mut label_written = false;
        if let Some(l) = label {
            let (ok, note) = label_after_gen(card, id, &l);
            label_written = ok;
            message.push(' ');
            message.push_str(&note);
        }
        Ok(GenResult {
            id,
            kind: "ec".to_string(),
            detail: curve,
            label_written,
            message,
            spki_hex: gak_spki_hex(&rsp),
        })
    })
}
#[cfg(test)]
mod tests {
    use super::*;

    /// Real C402 dump from an AES-256 key (firmware meta layout B).
    const AES_META: [u8; 32] = [
        0xA8, 0x1E, 0x30, 0x02, 0x0C, 0x00, 0x30, 0x08, 0x04, 0x01, 0x32, 0x03, 0x03, 0x07, 0xC0,
        0x10, 0xA0, 0x06, 0x30, 0x04, 0x02, 0x02, 0x01, 0x00, 0xA1, 0x06, 0x30, 0x04, 0x30, 0x02,
        0x04, 0x00,
    ];

    #[test]
    fn layout_b_label_empty_then_roundtrip() {
        // Empty UTF8String -> no label (N/A), but slot must be found.
        assert_eq!(parse_label(&AES_META), None);
        let patched = patch_label(&AES_META, "AES-Test").expect("layout B must patch");
        assert_eq!(parse_label(&patched), Some("AES-Test".to_string()));
        // Sibling fields untouched: key id "2" and size 256 still present.
        assert!(patched.windows(3).any(|w| w == [0x04, 0x01, 0x32]));
        assert!(patched.windows(4).any(|w| w == [0x02, 0x02, 0x01, 0x00]));
        assert_eq!(patched.len(), AES_META.len() - 2 + 10);
    }

    /// Real C40C dump from a secp521r1 key (firmware meta layout C: A0 outer).
    const EC_META: [u8; 29] = [
        0xA0, 0x1B, 0x30, 0x02, 0x0C, 0x00, 0x30, 0x09, 0x04, 0x02, 0x31, 0x32, 0x03, 0x03, 0x07,
        0x20, 0x80, 0xA1, 0x0A, 0x30, 0x08, 0x30, 0x02, 0x04, 0x00, 0x02, 0x02, 0x02, 0x10,
    ];

    #[test]
    fn layout_c_label_empty_then_roundtrip() {
        assert_eq!(parse_label(&EC_META), None);
        let patched = patch_label(&EC_META, "EC-521").expect("layout C must patch");
        assert_eq!(parse_label(&patched), Some("EC-521".to_string()));
        // Outer tag preserved, sibling id "12" untouched.
        assert_eq!(patched[0], 0xA0);
        assert!(patched.windows(4).any(|w| w == [0x04, 0x02, 0x31, 0x32]));
    }

    #[test]
    fn layout_a_label_replace() {
        // Minimal OpenSC-style PRKD: SEQ{ SEQ{ 80 "old" } SEQ{ 02 01 01 } }.
        let raw = [
            0x30, 0x0C, 0x30, 0x05, 0x80, 0x03, b'o', b'l', b'd', 0x30, 0x03, 0x02, 0x01, 0x01,
        ];
        assert_eq!(parse_label(&raw), Some("old".to_string()));
        let patched = patch_label(&raw, "new-label").expect("layout A must patch");
        assert_eq!(parse_label(&patched), Some("new-label".to_string()));
    }

    #[test]
    fn garbage_is_rejected() {
        assert_eq!(parse_label(&[0x00, 0x01, 0x02]), None);
        assert_eq!(patch_label(&[0x00, 0x01, 0x02], "x"), None);
        assert_eq!(patch_label(&[0x30, 0x02, 0x05, 0x00], "x"), None);
    }

    /// Real RSA PRKD (layout A) with label "Test 1024": trailing INT is 1024.
    /// Note the empty 0C tag after the label (exact pasted dump bytes).
    const RSA_PRKD: [u8; 38] = [
        0x30, 0x24, 0x30, 0x0D, 0x80, 0x09, b'T', b'e', b's', b't', b' ', b'1', b'0', b'2', b'4',
        0x0C, 0x00, 0x30, 0x07, 0x04, 0x01, 0x31, 0x03, 0x02, 0x02, 0x74, 0xA1, 0x0A, 0x30, 0x08,
        0x30, 0x02, 0x04, 0x00, 0x02, 0x02, 0x04, 0x00,
    ];

    /// Real AES meta (layout B) with label "Test AES": trailing INT is 256.
    const AES_PRKD_LABELED: [u8; 40] = [
        0xA8, 0x26, 0x30, 0x0A, 0x0C, 0x08, b'T', b'e', b's', b't', b' ', b'A', b'E', b'S', 0x30,
        0x08, 0x04, 0x01, 0x33, 0x03, 0x03, 0x07, 0xC0, 0x10, 0xA0, 0x06, 0x30, 0x04, 0x02, 0x02,
        0x01, 0x00, 0xA1, 0x06, 0x30, 0x04, 0x30, 0x02, 0x04, 0x00,
    ];

    #[test]
    fn trailing_size_per_layout() {
        assert_eq!(trailing_size(&RSA_PRKD), Some(1024));
        assert_eq!(trailing_size(&AES_PRKD_LABELED), Some(256));
        assert_eq!(trailing_size(&EC_META), Some(528));
        assert_eq!(parse_label(&RSA_PRKD), Some("Test 1024".to_string()));
        assert_eq!(parse_label(&AES_PRKD_LABELED), Some("Test AES".to_string()));
    }

    /// Usage BIT STRINGs from real dumps, verified against pkcs15-tool output:
    /// RSA 03 02 [02]74 -> 0x2E (decrypt, sign, signRecover, unwrap),
    /// AES 03 03 [07]C0 10 -> 0x0803, EC 03 03 [07]20 80 -> 0x0104 (sign, derive).
    /// Rule: reverse bits per content byte, assemble little-endian.
    #[test]
    fn usage_per_layout() {
        assert_eq!(parse_usage(&RSA_PRKD), Some(0x2E));
        assert_eq!(parse_usage(&AES_PRKD_LABELED), Some(0x0803));
        assert_eq!(parse_usage(&EC_META), Some(0x0104));
        assert_eq!(parse_usage(&[0x30, 0x00]), None);
    }

    #[test]
    fn cvc_pubkey_oid_and_prime() {
        // Synthetic CVC pubkey: 7F49{06 ECDSA-OID, 81 <secp256r1 prime>}.
        let prime = hex::decode("FFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF").unwrap();
        let mut inner = vec![0x06];
        inner.extend_from_slice(&encode_len(OID_ECDSA_SHA256.len()).unwrap());
        inner.extend_from_slice(&OID_ECDSA_SHA256);
        inner.extend_from_slice(&tlv(0x81, &prime).unwrap());
        let mut cvc = vec![0x7F, 0x21, 0x82, 0x00, 0x00];
        cvc.extend_from_slice(&[0x7F, 0x49]);
        cvc.extend_from_slice(&encode_len(inner.len()).unwrap());
        cvc.extend_from_slice(&inner);
        let len = (cvc.len() - 4) as u16;
        cvc[2] = (len >> 8) as u8;
        cvc[3] = (len & 0xFF) as u8;
        let found = find_tag(&cvc, &(0..cvc.len()), 0x7F49, 0).expect("7F49 found");
        let oid = find_tag(&cvc, &found.content, 0x06, 1).expect("OID found");
        assert_eq!(&cvc[oid.content.clone()], &OID_ECDSA_SHA256);
        let p = find_tag(&cvc, &found.content, 0x81, 1).expect("prime found");
        assert_eq!(curve_name_by_prime(&cvc[p.content.clone()]), Some("secp256r1"));
        assert_eq!(curve_bits("secp521r1"), Some(521));
        assert_eq!(curve_name_by_prime(&[0x00]), None);
    }

    #[test]
    fn gak_meta_tags() {
        let base = gak_rsa(2048).expect("base builds");
        // Counter + algorithms appended as top-level siblings.
        let meta = gak_meta(base.clone(), Some(1000), Some(vec![0x43, 0x33]), &RSA_ALGOS, "RSA")
            .expect("meta builds");
        assert!(meta.windows(6).any(|w| w == [0x90, 0x04, 0x00, 0x00, 0x03, 0xE8]));
        assert!(meta.windows(4).any(|w| w == [0x91, 0x02, 0x43, 0x33]));
        // Nothing appended without options.
        let plain = gak_meta(base.clone(), None, None, &RSA_ALGOS, "RSA").expect("plain builds");
        assert_eq!(plain, base);
        // Guardrails.
        assert!(gak_meta(base.clone(), Some(0), None, &RSA_ALGOS, "RSA").is_err());
        assert!(gak_meta(base.clone(), None, Some(vec![]), &RSA_ALGOS, "RSA").is_err());
        assert!(gak_meta(base.clone(), None, Some(vec![0x73]), &RSA_ALGOS, "RSA").is_err());
        assert!(gak_meta(base, None, Some(vec![0x73]), &EC_ALGOS, "EC").is_ok());
    }

    #[test]
    fn gak_rsa_shape() {
        let gak = gak_rsa(2048).expect("rsa gak builds");
        // Envelope: 5F29 01 00, 42 "UTCA00001", 7F49, 5F20 "UTCDUMMY00001".
        assert_eq!(&gak[0..4], &[0x5F, 0x29, 0x01, 0x00]);
        assert!(gak.windows(11).any(|w| w == b"\x42\x09UTCA00001"));
        assert!(gak.windows(16).any(|w| w == b"\x5F\x20\x0DUTCDUMMY00001"));
        // 7F49 contains OID_RSA + 02 02 0800 (2048 BE).
        let pos = gak.windows(2).position(|w| w == [0x7F, 0x49]).expect("7F49 present");
        let inner = &gak[pos..];
        assert!(inner.windows(10).any(|w| w == OID_RSA_V15_SHA256));
        assert!(inner.windows(4).any(|w| w == [0x02, 0x02, 0x08, 0x00]));
        assert!(gak.len() < 255, "rsa gak fits short APDU");
    }

    #[test]
    fn gak_ec_all_curves_build() {
        let curves = [
            "secp192r1", "secp256r1", "secp384r1", "secp521r1", "brainpoolP256r1", "brainpoolP384r1",
            "brainpoolP512r1", "secp192k1", "secp256k1", "curve25519", "curve448", "ed25519", "ed448",
        ];
        for curve in curves {
            let gak = gak_ec(curve).unwrap_or_else(|| panic!("{curve} builds"));
            assert_eq!(&gak[0..4], &[0x5F, 0x29, 0x01, 0x00], "{curve} envelope");
            assert!(gak.windows(10).any(|w| w == OID_ECDSA_SHA256), "{curve} ecdsa oid");
            assert!(gak.windows(16).any(|w| w == b"\x5F\x20\x0DUTCDUMMY00001"), "{curve} chr");
        }
        assert_eq!(gak_ec("nope"), None);
        // secp521r1 exceeds short APDU and needs the extended form.
        let g521 = gak_ec("secp521r1").expect("521 builds");
        assert!(g521.len() > 255, "521 gak needs extended APDU");
        let apdu = case4(0x00, 0x46, 0x01, 0x00, &g521).expect("extended encodes");
        assert_eq!(&apdu[0..4], &[0x00, 0x46, 0x01, 0x00]);
        assert_eq!(apdu[4], 0x00, "extended Lc lead byte");
        // Short path stays short.
        let g256 = gak_ec("secp256r1").expect("256 builds");
        assert!(g256.len() < 255);
        let apdu = case4(0x00, 0x46, 0x01, 0x00, &g256).expect("short encodes");
        assert_eq!(apdu[4] as usize, g256.len());
        assert_eq!(*apdu.last().unwrap(), 0x00);
    }

    /// Build a minimal synthetic X.509 cert: outer SEQ{ TBS SEQ{ SPKI },
    /// sigAlg SEQ, signature BIT STRING }. TBS carries only the SPKI —
    /// enough for structural validation + SPKI parsing, not a real cert.
    fn synth_cert(spki: &[u8]) -> Vec<u8> {
        let tbs = der_tlv(0x30, spki);
        let mut sig_alg_inner = der_oid(&[0x2A, 0x86, 0x48, 0x86, 0xF7, 0x0D, 0x01, 0x01, 0x0B]);
        sig_alg_inner.extend_from_slice(&der_tlv(0x05, &[]));
        let sig_alg = der_tlv(0x30, &sig_alg_inner);
        let sig = der_tlv(0x03, &[0x00, 0xDE, 0xAD]);
        let mut body = tbs;
        body.extend_from_slice(&sig_alg);
        body.extend_from_slice(&sig);
        der_tlv(0x30, &body)
    }

    fn synth_rsa_spki(modulus_len: usize) -> Vec<u8> {
        let modulus = vec![0xAA; modulus_len];
        let mut rsa = der_integer(&modulus);
        rsa.extend_from_slice(&der_integer(&[0x01, 0x00, 0x01]));
        let rsa = der_tlv(0x30, &rsa);
        let mut alg = der_oid(&OID_RSA_ENCRYPTION);
        alg.extend_from_slice(&der_tlv(0x05, &[]));
        let alg = der_tlv(0x30, &alg);
        let mut bit = vec![0x00];
        bit.extend_from_slice(&rsa);
        let bit = der_tlv(0x03, &bit);
        let mut spki = alg;
        spki.extend_from_slice(&bit);
        der_tlv(0x30, &spki)
    }

    fn synth_ec_spki() -> Vec<u8> {
        let mut point = vec![0x04];
        point.extend_from_slice(&[0x11; 32]);
        point.extend_from_slice(&[0x22; 32]);
        let mut alg = der_oid(&OID_EC_PUBLIC_KEY);
        alg.extend_from_slice(&der_oid(named_curve_oid("secp256r1").unwrap()));
        let alg = der_tlv(0x30, &alg);
        let mut bit = vec![0x00];
        bit.extend_from_slice(&point);
        let bit = der_tlv(0x03, &bit);
        let mut spki = alg;
        spki.extend_from_slice(&bit);
        der_tlv(0x30, &spki)
    }

    #[test]
    fn x509_rsa_parses_and_pem_roundtrips() {
        let cert = synth_cert(&synth_rsa_spki(256));
        match parse_x509_pubkey(&cert).expect("rsa cert parses") {
            X509Pubkey::Rsa { bits } => assert_eq!(bits, 2048),
            other => panic!("expected RSA, got {other:?}"),
        }
        // Raw DER accepted; PEM wrapping round-trips through extract.
        assert_eq!(extract_der_cert(&cert).expect("der ok"), cert);
        let pem = pem_wrap(&cert, "CERTIFICATE");
        assert_eq!(extract_der_cert(pem.as_bytes()).expect("pem ok"), cert);
        // List classification agrees.
        let (format, key_type, size, curve) = cert_blob_info(&cert);
        assert_eq!(format, "x509");
        assert_eq!(key_type, "RSA");
        assert_eq!(size, Some(2048));
        assert_eq!(curve, None);
    }

    #[test]
    fn x509_ec_parses_curve_and_bits() {
        let cert = synth_cert(&synth_ec_spki());
        match parse_x509_pubkey(&cert).expect("ec cert parses") {
            X509Pubkey::Ec { curve, .. } => assert_eq!(curve, Some("secp256r1")),
            _ => panic!("expected EC"),
        }
        let (format, key_type, size, curve) = cert_blob_info(&cert);
        assert_eq!(format, "x509");
        assert_eq!(key_type, "EC");
        assert_eq!(size, Some(256));
        assert_eq!(curve, Some("secp256r1".to_string()));
        assert_eq!(curve_name_by_oid(named_curve_oid("brainpoolP384r1").unwrap()), Some("brainpoolP384r1"));
        assert_eq!(curve_name_by_oid(&[0x00]), None);
    }

    #[test]
    fn csr_tbs_structure_and_validation() {
        let spki = synth_rsa_spki(256);
        let tbs = build_tbs_csr("pico.test", "ACME", "Lab", "de", &spki).expect("tbs builds");
        assert!(tbs.len() <= 1024, "TBS fits the card-side cap");
        // SEQ{ INTEGER 0, subject SEQ, SPKI SEQ, [0] empty attributes }.
        let outer = parse_tlv(&tbs, 0).expect("tbs parses");
        assert_eq!(outer.tag, 0x30);
        assert_eq!(outer.total.end, tbs.len());
        let kids = children(&tbs, &outer.content).expect("4 kids");
        assert_eq!(kids.len(), 4);
        assert_eq!((kids[0].tag, kids[1].tag, kids[2].tag, kids[3].tag), (0x02, 0x30, 0x30, 0xA0));
        assert_eq!(&tbs[kids[3].total.clone()], &[0xA0, 0x00]);
        // Subject: 4 RDNs (CN, O, OU, C); C uses PrintableString.
        let rdns = children(&tbs, &kids[1].content).expect("rdns");
        assert_eq!(rdns.len(), 4);
        let last = children(&tbs, &rdns[3].content).expect("c atv");
        assert_eq!(last.len(), 1);
        let atv = children(&tbs, &last[0].content).expect("oid+value");
        assert_eq!(&tbs[atv[0].content.clone()], &OID_AT_C);
        assert_eq!(atv[1].tag, 0x13);
        // SPKI round-trips through the SPKI parser (RSA 2048).
        let spki_back = &tbs[kids[2].total.clone()];
        assert_eq!(parse_spki_pubkey(spki_back), Some(("RSA".to_string(), 2048, None)));
        // Minimal subject: CN only.
        let mini = build_tbs_csr("x", "", "", "", &spki).expect("mini builds");
        let kids = children(&mini, &parse_tlv(&mini, 0).unwrap().content).unwrap();
        assert_eq!(children(&mini, &kids[1].content).unwrap().len(), 1);
        // Rejections.
        assert!(build_tbs_csr("", "", "", "", &spki).is_err());
        assert!(build_tbs_csr(&"a".repeat(65), "", "", "", &spki).is_err());
        assert!(build_tbs_csr("ok", "", "", "USA", &spki).is_err());
        assert!(build_tbs_csr("ok", "", "", "U1", &spki).is_err());
        assert!(build_tbs_csr("ok\nbad", "", "", "", &spki).is_err());
        assert!(build_tbs_csr("ok", &"b".repeat(65), "", "", &spki).is_err());
    }

    #[test]
    fn csr_spki_vectors() {
        let rsa = synth_rsa_spki(128);
        assert_eq!(parse_spki_pubkey(&rsa), Some(("RSA".to_string(), 1024, None)));
        let ec = synth_ec_spki();
        assert_eq!(
            parse_spki_pubkey(&ec),
            Some(("EC".to_string(), 256, Some("secp256r1".to_string())))
        );
        assert_eq!(parse_spki_pubkey(b"garbage"), None);
        assert_eq!(parse_spki_pubkey(&der_tlv(0x30, &[])), None);
    }

    #[test]
    fn csr_assembly_shape() {
        let spki = synth_ec_spki();
        let tbs = build_tbs_csr("ec.test", "", "", "", &spki).expect("tbs builds");
        let fake_sig = vec![0x30, 0x44, 0x02, 0x20];
        let csr = assemble_csr(&tbs, "EC", &fake_sig);
        // SEQ{ TBS SEQ, sigAlg SEQ, BIT STRING }; TBS byte-identical inside.
        let outer = parse_tlv(&csr, 0).expect("csr parses");
        assert_eq!(outer.total.end, csr.len());
        let kids = children(&csr, &outer.content).expect("3 kids");
        assert_eq!((kids[0].tag, kids[1].tag, kids[2].tag), (0x30, 0x30, 0x03));
        assert_eq!(&csr[kids[0].total.clone()], &tbs);
        // EC sigAlg has no NULL params; RSA does.
        let alg = children(&csr, &kids[1].content).expect("alg kids");
        assert_eq!(alg.len(), 1);
        assert_eq!(&csr[alg[0].content.clone()], &OID_ECDSA_SHA256_X509);
        let rsa_csr = assemble_csr(&tbs, "RSA", &fake_sig);
        let rkids = children(&rsa_csr, &parse_tlv(&rsa_csr, 0).unwrap().content).unwrap();
        let ralg = children(&rsa_csr, &rkids[1].content).unwrap();
        assert_eq!(ralg.len(), 2);
        assert_eq!(&rsa_csr[ralg[0].content.clone()], &OID_SHA256_RSA);
        assert_eq!(ralg[1].tag, 0x05);
        // PEM label for CSRs.
        let pem = pem_wrap(&csr, "CERTIFICATE REQUEST");
        assert!(pem.starts_with("-----BEGIN CERTIFICATE REQUEST-----\n"));
    }

    #[test]
    fn init_tlv_vectors() {
        // Minimal: PINs + retries, no DKEK tag. SO-PIN is 16 hex -> 8 bytes.
        let t = build_init_tlv("123456", "3031323334353637", 3, DkekSetup::None).expect("minimal builds");
        let mut expected = vec![0x81, 0x06];
        expected.extend_from_slice(b"123456");
        expected.extend_from_slice(&[0x82, 0x08, 0x30, 0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37]);
        expected.extend_from_slice(&[0x91, 0x01, 0x03]);
        assert_eq!(t, expected);
        // Random DKEK appends 92 01 00; N slots append 92 01 N.
        let r = build_init_tlv("123456", "3031323334353637", 3, DkekSetup::Random).expect("random builds");
        assert_eq!(&r[r.len() - 3..], &[0x92, 0x01, 0x00]);
        let s = build_init_tlv("123456", "3031323334353637", 3, DkekSetup::Slots(2)).expect("slots builds");
        assert_eq!(&s[s.len() - 3..], &[0x92, 0x01, 0x02]);
        assert!(s.len() < 255, "init TLV fits short APDU");
        // Rejections.
        assert!(build_init_tlv("12345", "3031323334353637", 3, DkekSetup::None).is_err());
        assert!(build_init_tlv(&"1".repeat(17), "3031323334353637", 3, DkekSetup::None).is_err());
        assert!(build_init_tlv("123456", "1234567", 3, DkekSetup::None).is_err());
        assert!(build_init_tlv("123456", &"1".repeat(17), 3, DkekSetup::None).is_err());
        assert!(build_init_tlv("123456", "ZZZZZZZZZZZZZZZZ", 3, DkekSetup::None).is_err());
        assert!(build_init_tlv("123456", "3031323334353637", 0, DkekSetup::None).is_err());
        assert!(build_init_tlv("123456", "3031323334353637", 16, DkekSetup::None).is_err());
        assert!(build_init_tlv("123456", "3031323334353637", 3, DkekSetup::Slots(0)).is_err());
        assert!(build_init_tlv("123456", "3031323334353637", 3, DkekSetup::Slots(9)).is_err());
        assert!(build_init_tlv("1234\n6", "3031323334353637", 3, DkekSetup::None).is_err());
    }

    #[test]
    fn dkek_status_vectors() {
        // [total, remaining, KCV×8] (+ trailing XKEK bytes).
        let s = parse_dkek_status(0, "0302AABBCCDDEEFF0011").expect("parses");
        assert_eq!((s.domain, s.total, s.remaining), (0, 3, 2));
        assert_eq!(s.kcv_hex, "AABBCCDDEEFF0011");
        assert!(!s.has_xkek);
        let x = parse_dkek_status(1, "01000000000000000000DEADBEEF").expect("xkek parses");
        assert!(x.has_xkek);
        assert_eq!(x.total, 1);
        assert_eq!(x.remaining, 0);
        assert!(parse_dkek_status(0, "0102").is_err());
        assert!(parse_dkek_status(0, "ZZ").is_err());
    }

    #[test]
    fn x509_ed25519_parses() {
        // EdDSA SPKI: algorithm OID only, BIT STRING holds 32 raw bytes.
        let alg = der_oid(&[0x2B, 0x65, 0x70]);
        let alg = der_tlv(0x30, &alg);
        let mut raw = vec![0x00];
        raw.extend_from_slice(&[0x42; 32]);
        let bit = der_tlv(0x03, &raw);
        let mut spki = alg;
        spki.extend_from_slice(&bit);
        let cert = synth_cert(&der_tlv(0x30, &spki));
        match parse_x509_pubkey(&cert).expect("ed25519 cert parses") {
            X509Pubkey::Ec { curve, .. } => assert_eq!(curve, Some("ed25519")),
            _ => panic!("expected EC/Ed"),
        }
        let (format, key_type, size, curve) = cert_blob_info(&cert);
        assert_eq!((format.as_str(), key_type.as_str()), ("x509", "EC"));
        assert_eq!(size, Some(256));
        assert_eq!(curve, Some("ed25519".to_string()));
        assert_eq!(extract_der_cert(&cert).expect("der ok"), cert);
    }

    #[test]
    fn x509_rejects_structural_garbage() {
        let cert = synth_cert(&synth_rsa_spki(128));
        // Truncated outer SEQUENCE.
        assert!(parse_x509_pubkey(&cert[..cert.len() - 5]).is_none());
        assert!(extract_der_cert(&cert[..cert.len() - 5]).is_err());
        // Wrong child count (drop the signature, re-wrap the first two).
        let outer = parse_tlv(&cert, 0).unwrap();
        let kids = children(&cert, &outer.content).unwrap();
        assert_eq!(kids.len(), 3);
        let two_children = cert[kids[0].total.start..kids[1].total.end].to_vec();
        let two = der_tlv(0x30, &two_children);
        assert!(parse_x509_pubkey(&two).is_none());
        assert!(extract_der_cert(&two).is_err());
        // Arbitrary non-cert SEQUENCE (e.g. key-like SEQ of INTEGERs).
        let mut fake = der_integer(&[0xAA; 32]);
        fake.extend_from_slice(&der_integer(&[0x01, 0x00, 0x01]));
        let fake = der_tlv(0x30, &fake);
        assert!(parse_x509_pubkey(&fake).is_none());
        assert!(extract_der_cert(&fake).is_err());
        // Not DER at all / bad PEM.
        assert!(extract_der_cert(b"hello").is_err());
        assert!(extract_der_cert(b"-----BEGIN CERTIFICATE-----\n!!!\n-----END CERTIFICATE-----\n").is_err());
        // Unknown algorithm OID inside a well-formed envelope.
        let mut alg = der_oid(&[0x2A, 0x86, 0x48, 0xCE, 0x38, 0x04, 0x01]);
        alg.extend_from_slice(&der_tlv(0x05, &[]));
        let alg = der_tlv(0x30, &alg);
        let bit = der_tlv(0x03, &[0x00, 0x01, 0x02, 0x03]);
        let mut spki = alg;
        spki.extend_from_slice(&bit);
        let exotic = synth_cert(&der_tlv(0x30, &spki));
        assert!(parse_x509_pubkey(&exotic).is_none());
        assert!(extract_der_cert(&exotic).is_err());
        // CVC blobs are not X.509.
        assert_eq!(cert_blob_info(&[0x7F, 0x21, 0x00]).0, "unknown");
    }

    #[test]
    fn base64_vectors() {
        assert_eq!(base64_encode(b""), "");
        assert_eq!(base64_encode(b"M"), "TQ==");
        assert_eq!(base64_encode(b"Ma"), "TWE=");
        assert_eq!(base64_encode(b"Man"), "TWFu");
        assert_eq!(base64_decode("TQ=="), Some(b"M".to_vec()));
        assert_eq!(base64_decode("TWE="), Some(b"Ma".to_vec()));
        assert_eq!(base64_decode("TWFu"), Some(b"Man".to_vec()));
        assert_eq!(base64_decode("TQ"), None);
        assert_eq!(base64_decode("T==="), None);
        assert_eq!(base64_decode("TQ==TQ=="), Some(b"MM".to_vec()));
        assert_eq!(base64_decode("****"), None);
        assert_eq!(base64_decode(" TQ==\n"), Some(b"M".to_vec()));
    }

    #[test]
    fn update_payload_chunk_math() {
        // 200-byte chunk at offset 0: offset TLV + 81-length, fits short APDU.
        let chunk = vec![0x55; 200];
        let apdu = update_payload(0xCE, 0x01, 0, &chunk).expect("encodes");
        assert_eq!(&apdu[0..4], &[0x00, 0xD7, 0xCE, 0x01]);
        assert_eq!(apdu[4] as usize, apdu.len() - 6, "Lc covers payload + Le");
        assert_eq!(&apdu[5..10], &[0x54, 0x02, 0x00, 0x00, 0x53]);
        assert_eq!(&apdu[10..12], &[0x81, 200]);
        assert_eq!(*apdu.last().unwrap(), 0x00);
        assert!(apdu.len() <= 255 + 6);
        // Non-zero offset encodes big-endian.
        let small = update_payload(0xCE, 0x01, 0x1234, &[0x01; 10]).expect("encodes");
        assert_eq!(&small[5..10], &[0x54, 0x02, 0x12, 0x34, 0x53]);
        // Guardrails.
        assert_eq!(update_payload(0xCE, 0x01, 0, &[]), None);
        assert_eq!(update_payload(0xCE, 0x01, 0, &vec![0x00; 201]), None);
        assert_eq!(update_payload(0xCE, 0x01, 0x1_0000, &[0x01]), None);
        // PEM wrap shape: header/footer + decodable body.
        let raw = [0x30, 0x03, 0x02, 0x01, 0x05];
        let pem = pem_wrap(&raw, "CERTIFICATE");
        assert!(pem.starts_with("-----BEGIN CERTIFICATE-----\n"));
        assert!(pem.ends_with("-----END CERTIFICATE-----\n"));
        let body: String = pem.lines().filter(|l| !l.starts_with("-----")).collect();
        assert_eq!(base64_decode(&body), Some(raw.to_vec()));
    }
}

#[derive(Debug, Serialize, Clone)]
pub struct DeleteResult {
    pub id: u8,
    /// FIDs actually deleted, e.g. ["CC01", "C401"].
    pub deleted: Vec<String>,
    /// FIDs that were already absent (not an error).
    pub missing: Vec<String>,
}

/// Delete a key group: `00 E4` per file (CC + C4 + CE of the id).
/// Best-effort per file (missing files are reported, not fatal).
/// Needs User-PIN authentication (same-connection VERIFY).
/// ID 0 (device key) is refused client-side too — the firmware
/// would refuse it as well (EF_KEY_DEV).
#[tauri::command]
pub fn delete_key(
    reader: String,
    id: u8,
    pin: Option<String>,
) -> Result<DeleteResult, DeviceError> {
    if id == 0 {
        return Err(DeviceError::new(
            "RefusedDelete",
            "The device key (ID 0) cannot be deleted".to_string(),
            "It is required for attestation.",
        ));
    }
    with_card(&reader, |card| {
        select_hsm(card)?;
        if let Some(ref p) = pin {
            verify_pin(card, p)?;
        }
        let mut deleted = Vec::new();
        let mut missing = Vec::new();
        for hi in [0xCC, 0xC4, 0xCE] {
            let fid = format!("{hi:02X}{id:02X}");
            let rsp = transmit_on_card(card, &[0x00, 0xE4, 0x00, 0x00, 0x02, hi, id, 0x00])?;
            if rsp.sw_hex == "9000" {
                deleted.push(fid);
            } else if rsp.sw_hex == "6A82" {
                missing.push(fid);
            } else if rsp.sw_hex == "6982" {
                return Err(DeviceError::auth_required(
                    "DeleteAuth",
                    format!("Delete needs User-PIN authentication (SW=6982, file {fid})"),
                ));
            } else {
                return Err(DeviceError::new(
                    "DeleteFailed",
                    format!("Delete of {fid} failed with SW={}", rsp.sw_hex),
                    "Nothing further was attempted after this file. Check the key list.",
                ));
            }
        }
        Ok(DeleteResult { id, deleted, missing })
    })
}
