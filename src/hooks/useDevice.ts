import { useEffect, useRef, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import { toast } from "sonner";
import {
  tauriApi,
  type CardStatus,
  type DeviceError,
  type FirmwareVersion,
  type FlashInfo,
  type FlashProgress,
  type FlashResult,
  type PinStatus,
  type PlatformInfo,
  type RtcTime,
  type SecureInfo,
  type SerialInfo,
} from "../lib/tauri";

export interface FieldErrors {
  rtc: string | null;
  version: string | null;
  flash: string | null;
  secure: string | null;
  pin: string | null;
  sopin: string | null;
  serial: string | null;
  platform: string | null;
}

export interface LiveState {
  present: boolean;
  reader: string | null;
  atrHex: string | null;
  protocol: string | null;
  rtc: RtcTime | null;
  rtcDate: Date | null;
  version: FirmwareVersion | null;
  flash: FlashInfo | null;
  secure: SecureInfo | null;
  pin: PinStatus | null;
  sopin: PinStatus | null;
  serial: SerialInfo | null;
  platform: PlatformInfo | null;
  fieldErrors: FieldErrors;
  /** Human-readable reason why no live data is available (no board connected). */
  transportNote: string | null;
}

const EMPTY_ERRORS: FieldErrors = { rtc: null, version: null, flash: null, secure: null, pin: null, sopin: null, serial: null, platform: null };

const EMPTY_LIVE: LiveState = {
  present: false,
  reader: null,
  atrHex: null,
  protocol: null,
  rtc: null,
  rtcDate: null,
  version: null,
  flash: null,
  secure: null,
  pin: null,
  sopin: null,
  serial: null,
  platform: null,
  fieldErrors: EMPTY_ERRORS,
  transportNote: "Probing for device…",
};

function errText(e: unknown): string {
  const err = e as DeviceError;
  if (err && typeof err.code === "string") return `${err.code}${"message" in err && err.message ? `: ${err.message}` : ""}`;
  return String(e);
}

function parseRtc(display: string): Date | null {
  // "YYYY-MM-DD HH:MM:SS"
  const m = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})$/.exec(display);
  if (!m) return null;
  const d = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function useDevice() {
  const [bootsel, setBootsel] = useState(false);
  const [live, setLive] = useState<LiveState>(EMPTY_LIVE);
  // User-PIN, memory-only: never persisted, never logged, cleared on disconnect.
  // (Kept for upcoming authenticated commands; the rescue clock needs no auth.)
  const [sessionPin, setSessionPin] = useState<string | null>(null);
  const [pinRequired, setPinRequired] = useState(false);
  // Ticker so the device clock re-renders every second.
  const [, setTick] = useState(0);
  // Host timestamp of the last RTC sample — the displayed time advances locally between polls.
  const liveAt = useRef(Date.now());
  const pollRef = useRef(0);

  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    liveAt.current = Date.now();
  }, [live.rtcDate]);

  // Live polling, two tiers for speed:
  // - fast (every 5 s): presence, ATR/protocol, RTC, PIN/SO-PIN retries.
  // - slow: version, flash, secure, serial, platform — once per reader
  //   connection plus on manual refresh / after mutations (probeNonce).
  // Static data re-read every cycle was the main perf drain (~20 APDUs).
  const [probeNonce, setProbeNonce] = useState(0);
  const slowDoneFor = useRef<{ key: string; nonce: number } | null>(null);
  // Auto clock-sync: at most 3 attempts per connection (device may be busy).
  const clockSync = useRef<{ key: string; tries: number } | null>(null);
  useEffect(() => {
    let cancelled = false;
    const myPoll = ++pollRef.current;

    async function probe() {
      try {
        const readers = await tauriApi.listReaders();
        if (cancelled || myPoll !== pollRef.current) return;
        if (readers.length === 0) {
          setLive((l) => ({
            ...l,
            ...EMPTY_LIVE,
            transportNote: "No smartcard readers found. Connect the Pico HSM or start pcscd.",
          }));
          setSessionPin(null);
          clockSync.current = null;
          slowDoneFor.current = null;
          return;
        }
        const reader = readers[0];
        let status: CardStatus;
        try {
          status = await tauriApi.cardStatus(reader);
        } catch (e) {
          if (cancelled || myPoll !== pollRef.current) return;
          const err = e as DeviceError;
          setLive((l) => ({
            ...l,
            ...EMPTY_LIVE,
            reader,
            transportNote: err.hint || err.message,
          }));
          setSessionPin(null);
          clockSync.current = null;
          slowDoneFor.current = null;
          return;
        }
        // Best-effort enrichment: each may fail independently (busy, old fw, uninitialized).
        // Errors are kept per field so Details/Diagnose can show them (remote debugging).
        // Fast tier always; slow tier only for new connections or explicit refresh.
        const enrichKey = `${status.reader}|${status.atr_hex}`;
        const needSlow = slowDoneFor.current?.key !== enrichKey || slowDoneFor.current.nonce !== probeNonce;
        const fastSettled = await Promise.all([
          tauriApi.rtcTime(reader).then(
            (v) => ({ ok: true as const, v }),
            (e) => ({ ok: false as const, e }),
          ),
          tauriApi.pinRetries(reader).then(
            (v) => ({ ok: true as const, v }),
            (e) => ({ ok: false as const, e }),
          ),
          tauriApi.sopinRetries(reader).then(
            (v) => ({ ok: true as const, v }),
            (e) => ({ ok: false as const, e }),
          ),
        ]);
        if (cancelled || myPoll !== pollRef.current) return;
        const [rtcR, pinR, sopinR] = fastSettled;
        const rtc = rtcR.ok ? rtcR.v : null;
        type Settled<T> = { ok: true; v: T } | { ok: false; e: unknown };
        let versionR: Settled<FirmwareVersion> | null = null;
        let flashR: Settled<FlashInfo> | null = null;
        let secureR: Settled<SecureInfo> | null = null;
        let serialR: Settled<SerialInfo> | null = null;
        let platformR: Settled<PlatformInfo> | null = null;
        if (needSlow) {
          [versionR, flashR, secureR, serialR, platformR] = await Promise.all([
            tauriApi.version(reader).then(
              (v) => ({ ok: true as const, v }),
              (e) => ({ ok: false as const, e }),
            ),
            tauriApi.flash(reader).then(
              (v) => ({ ok: true as const, v }),
              (e) => ({ ok: false as const, e }),
            ),
            tauriApi.secure(reader).then(
              (v) => ({ ok: true as const, v }),
              (e) => ({ ok: false as const, e }),
            ),
            tauriApi.serial(reader).then(
              (v) => ({ ok: true as const, v }),
              (e) => ({ ok: false as const, e }),
            ),
            tauriApi.platform(reader).then(
              (v) => ({ ok: true as const, v }),
              (e) => ({ ok: false as const, e }),
            ),
          ]);
          if (cancelled || myPoll !== pollRef.current) return;
          slowDoneFor.current = { key: enrichKey, nonce: probeNonce };
        }
        setLive((prev) => {
          // Keep slow-tier values across fast cycles of the same connection.
          const sameConn = prev.present && prev.reader === status.reader;
          return {
            present: true,
            reader: status.reader,
            atrHex: status.atr_hex,
            protocol: status.protocol,
            rtc,
            rtcDate: rtc ? parseRtc(rtc.display) : null,
            version: versionR ? (versionR.ok ? versionR.v : null) : sameConn ? prev.version : null,
            flash: flashR ? (flashR.ok ? flashR.v : null) : sameConn ? prev.flash : null,
            secure: secureR ? (secureR.ok ? secureR.v : null) : sameConn ? prev.secure : null,
            pin: pinR.ok ? pinR.v : null,
            sopin: sopinR.ok ? sopinR.v : null,
            serial: serialR ? (serialR.ok ? serialR.v : null) : sameConn ? prev.serial : null,
            platform: platformR ? (platformR.ok ? platformR.v : null) : sameConn ? prev.platform : null,
            fieldErrors: {
              rtc: rtcR.ok ? null : errText(rtcR.e),
              version: versionR ? (versionR.ok ? null : errText(versionR.e)) : sameConn ? prev.fieldErrors.version : null,
              flash: flashR ? (flashR.ok ? null : errText(flashR.e)) : sameConn ? prev.fieldErrors.flash : null,
              secure: secureR ? (secureR.ok ? null : errText(secureR.e)) : sameConn ? prev.fieldErrors.secure : null,
              pin: pinR.ok ? null : errText(pinR.e),
              sopin: sopinR.ok ? null : errText(sopinR.e),
              serial: serialR ? (serialR.ok ? null : errText(serialR.e)) : sameConn ? prev.fieldErrors.serial : null,
              platform: platformR ? (platformR.ok ? null : errText(platformR.e)) : sameConn ? prev.fieldErrors.platform : null,
            },
            transportNote: null,
          };
        });
        // Auto clock-sync: the RTC is volatile (no battery) and resets on
        // power loss, so a fresh connection usually reports RtcNotSet (or a
        // stale pre-2024 epoch). Set host time automatically, max 3 tries
        // per connection — the manual Sync button remains for retries/drift.
        const clockMissing =
          (!rtcR.ok && (rtcR.e as DeviceError)?.code === "RtcNotSet") ||
          (rtc !== null && rtc.year < 2024);
        const connKey = `${status.reader}|${status.atr_hex}`;
        const syncState = clockSync.current;
        if (clockMissing && (syncState?.key !== connKey || syncState.tries < 3)) {
          clockSync.current = { key: connKey, tries: (syncState?.key === connKey ? syncState.tries : 0) + 1 };
          void (async () => {
            try {
              const now = new Date();
              await tauriApi.setRtc(status.reader, {
                year: now.getFullYear(),
                month: now.getMonth() + 1,
                day: now.getDate(),
                weekday: now.getDay(),
                hour: now.getHours(),
                minute: now.getMinutes(),
                second: now.getSeconds(),
              });
              // Silent on purpose: auto-sync runs on every connect.
              // The manual Sync button (Diagnose) still reports success/failure.
              setProbeNonce((n) => n + 1);
            } catch {
              // Attempts exhausted via the tries counter; Diagnose keeps the
              // error and the manual Sync button stays available.
            }
          })();
        }
      } catch (e) {
        if (cancelled || myPoll !== pollRef.current) return;
        const err = e as DeviceError;
        setLive((l) => ({
          ...l,
          ...EMPTY_LIVE,
          transportNote: err.hint || err.message || "PC/SC unavailable.",
        }));
      }
    }

    void probe();
    const t = setInterval(probe, 5000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [probeNonce]);

  const online = live.present;
  // Clock: device RTC, advanced locally between polls; null (→ "--") without a device.
  const now = !live.rtcDate ? null : new Date(live.rtcDate.getTime() + (Date.now() - liveAt.current));
  // Version: rescue applet first, SELECT-FCP as fallback.
  // No device -> nulls across the board (UI shows N/A badges).
  const versionDisplay = live.platform?.version ?? live.version?.display ?? null;
  const serialDisplay = live.serial?.serial ?? live.platform?.board_hex ?? null;
  // Device options word from SELECT-FCP tag 0x85 (see sc_hsm.h HSM_OPT_*).
  const optsWord = live.version ? parseInt(live.version.opts_hex, 16) : null;
  const securityOpts =
    optsWord === null
      ? null
      : {
          pressConfirm: (optsWord & 0x0100) !== 0,
          keyCounter: (optsWord & 0x0200) !== 0,
          secureLock: (optsWord & 0x0400) !== 0,
          resetRetryCounter: (optsWord & 0x0001) !== 0,
          resetOnly: (optsWord & 0x0020) !== 0,
        };
  // Firmware retry maxima (constants, no device source): UserPIN 3, SOPIN 15.
  const pinMax = { user: 3, so: 15 };
  // Init heuristic: device cert readable + User-PIN answering -> initialized.
  // Fresh-device behavior unverified (spike) — otherwise honestly Unknown.
  const initState: "initialized" | "unknown" =
    live.present && live.serial !== null && live.pin !== null ? "initialized" : "unknown";

  // Memory from the rescue applet (FLASH INFO). Firmware size is only
  // reported on Pico targets (24-byte response) — otherwise N/A.
  const firmwareLive = live.flash?.firmware_bytes != null;

  // Reconnect detection: leaving BOOTSEL/normal reboot returns as a fresh
  // presence — drop the bootsel view back to normal with a toast.
  const prevOnline = useRef(online);
  useEffect(() => {
    if (online && !prevOnline.current && bootsel) {
      setBootsel(false);
      toast.success("Device back online", { description: "Reconnected in normal mode." });
    }
    prevOnline.current = online;
  }, [online, bootsel]);

  async function rebootDevice(toBootsel: boolean): Promise<string> {
    if (!live.reader) throw new Error("No device connected.");
    return tauriApi.reboot(live.reader, toBootsel);
  }

  async function syncClock(dt: import("../lib/tauri").HostDatetime): Promise<string> {
    if (!live.reader) throw new Error("No device connected.");
    const msg = await tauriApi.setRtc(live.reader, dt);
    setProbeNonce((n) => n + 1);
    return msg;
  }

  /** Re-read everything immediately instead of waiting for the interval. */
  function refresh() {
    setProbeNonce((n) => n + 1);
  }

  /** Flash a UF2 image with live progress. Long-running (reboots + waits). */
  async function flashFirmware(path: string, onProgress: (p: FlashProgress) => void): Promise<FlashResult> {
    if (!live.reader) throw new Error("No device connected.");
    const unlisten = await listen<FlashProgress>("flash-progress", (e) => onProgress(e.payload));
    try {
      const res = await tauriApi.flashUf2(live.reader, path);
      setProbeNonce((n) => n + 1);
      return res;
    } finally {
      unlisten();
    }
  }

  /** Write dynamic options (press-confirm, key-counter) with session PIN. */
  async function saveDynops(pressConfirm: boolean, keyCounter: boolean): Promise<string> {
    if (!live.reader) throw new Error("No device connected.");
    try {
      const msg = await tauriApi.setDynops(live.reader, pressConfirm, keyCounter, sessionPin);
      setProbeNonce((n) => n + 1);
      return msg;
    } catch (e) {
      if ((e as DeviceError)?.auth_required) requestPin();
      throw e;
    }
  }

  /** Rename a description record (PRKD/CD/DCOD) with session PIN. */
  async function renameLabel(fid: string, label: string): Promise<string> {
    if (!live.reader) throw new Error("No device connected.");
    try {
      const msg = await tauriApi.setLabel(live.reader, fid, label, sessionPin);
      return msg;
    } catch (e) {
      if ((e as DeviceError)?.auth_required) requestPin();
      throw e;
    }
  }

  /** Delete a key group (CC + C4 + CE) with session PIN. */
  async function deleteKey(id: number): Promise<import("../lib/tauri").DeleteResult> {
    if (!live.reader) throw new Error("No device connected.");
    try {
      const res = await tauriApi.deleteKey(live.reader, id, sessionPin);
      return res;
    } catch (e) {
      if ((e as DeviceError)?.auth_required) requestPin();
      throw e;
    }
  }

  /** Delete a standalone CA certificate with session PIN. */
  async function deleteCert(fid: string): Promise<string> {
    if (!live.reader) throw new Error("No device connected.");
    try {
      return await tauriApi.deleteCert(live.reader, fid, sessionPin);
    } catch (e) {
      if ((e as DeviceError)?.auth_required) requestPin();
      throw e;
    }
  }

  /** Export a certificate's public key as PEM with session PIN. */
  async function exportCert(fid: string): Promise<string> {
    if (!live.reader) throw new Error("No device connected.");
    try {
      return await tauriApi.exportPubkey(live.reader, fid, sessionPin);
    } catch (e) {
      if ((e as DeviceError)?.auth_required) requestPin();
      throw e;
    }
  }

  /** Import an X.509 certificate file onto a key id with session PIN. */
  async function importCert(id: number, fileBytes: number[]): Promise<string> {
    if (!live.reader) throw new Error("No device connected.");
    try {
      const msg = await tauriApi.importCert(live.reader, id, fileBytes, sessionPin);
      setProbeNonce((n) => n + 1);
      return msg;
    } catch (e) {
      if ((e as DeviceError)?.auth_required) requestPin();
      throw e;
    }
  }

  /** Download a stored X.509 certificate as PEM with session PIN. */
  async function downloadCert(fid: string): Promise<string> {
    if (!live.reader) throw new Error("No device connected.");
    try {
      return await tauriApi.downloadCert(live.reader, fid, sessionPin);
    } catch (e) {
      if ((e as DeviceError)?.auth_required) requestPin();
      throw e;
    }
  }

  /** Generate a key with session PIN. Long RSA sizes block for minutes. */
  async function generateKey(
    kind: "aes" | "rsa" | "ec",
    param: number | string,
    label: string | null,
    opts?: { counter?: number | null; algorithms?: number[] | null },
  ): Promise<import("../lib/tauri").GenResult> {
    if (!live.reader) throw new Error("No device connected.");
    try {
      const counter = opts?.counter ?? null;
      const algos = opts?.algorithms ?? null;
      const res =
        kind === "aes"
          ? await tauriApi.genAes(live.reader, param as number, label, sessionPin)
          : kind === "rsa"
            ? await tauriApi.genRsa(live.reader, param as number, label, sessionPin, counter, algos)
            : await tauriApi.genEc(live.reader, param as string, label, sessionPin, counter, algos);
      return res;
    } catch (e) {
      if ((e as DeviceError)?.auth_required) requestPin();
      throw e;
    }
  }
  /** Verify the User-PIN. Stored in RAM only, cleared on disconnect. Never logged. */
  async function login(pin: string): Promise<string> {
    if (!live.reader) throw new Error("No device connected.");
    const msg = await tauriApi.login(live.reader, pin);
    setSessionPin(pin);
    setPinRequired(false);
    setProbeNonce((n) => n + 1);
    return msg;
  }

  function logout() {
    setSessionPin(null);
    setPinRequired(false);
  }

  /** "Later" on the PIN dialog: just close it. */
  function dismissPin() {
    setPinRequired(false);
  }

  /** Manual unlock trigger: opens the dialog. */
  function requestPin() {
    setPinRequired(true);
  }

  const device = {
    product: live.platform?.product ?? null,
    platform: live.platform?.platform ?? null,
    version: versionDisplay,
    serial: serialDisplay,
    connection: !live.present ? null : bootsel ? "RESCUE" : "NORMAL",
  };
  const memory = live.flash
    ? {
        freeBytes: live.flash.free_bytes,
        usedBytes: live.flash.used_bytes,
        totalBytes: live.flash.total_bytes,
        fileCount: live.flash.file_count,
        filesystemBytes: live.flash.flash_bytes,
        firmwareBytes: live.flash.firmware_bytes ?? null,
      }
    : null;

  return {
    online,
    bootsel,
    setBootsel,
    rebootDevice,
    syncClock,
    refresh,
    flashFirmware,
    saveDynops,
    renameLabel,
    deleteKey,
    deleteCert,
    exportCert,
    importCert,
    downloadCert,
    generateKey,
    login,
    logout,
    unlocked: sessionPin !== null,
    pinRequired,
    requestPin,
    dismissPin,
    now,
    firmwareLive,
    securityOpts,
    pinMax,
    initState,
    live,
    transportNote: live.transportNote,
    device,
    memory,
  };
}

export type DeviceState = ReturnType<typeof useDevice>;
