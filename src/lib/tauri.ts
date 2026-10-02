import { invoke } from "@tauri-apps/api/core";

export interface DeviceError {
  code: string;
  message: string;
  hint: string;
  auth_required?: boolean;
}

export interface CardStatus {
  reader: string;
  atr_hex: string;
  protocol: string;
}

export interface RtcTime {
  display: string;
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
  second: number;
}

export interface HostDatetime {
  year: number;
  month: number;
  day: number;
  /** 0 = Sunday .. 6 = Saturday */
  weekday: number;
  hour: number;
  minute: number;
  second: number;
}

export interface FirmwareVersion {
  display: string;
  major: number;
  minor: number;
  opts_hex: string;
}

export interface PinStatus {
  retries: number;
  blocked: boolean;
  sw_hex: string;
}

export interface SerialInfo {
  serial: string;
  raw_len: number;
}

export interface TransmitResult {
  data_hex: string;
  sw1: number;
  sw2: number;
  sw_hex: string;
}

export interface PlatformInfo {
  platform: string;
  product: string;
  version: string;
  board_hex: string;
  raw_hex: string;
}

export interface FlashInfo {
  free_bytes: number;
  used_bytes: number;
  total_bytes: number;
  file_count: number;
  flash_bytes: number;
  firmware_bytes: number | null;
  raw_hex: string;
}

export interface SecureInfo {
  secure_boot: boolean;
  locked: boolean;
  boot_key: number;
}

export interface KeyEntry {
  fid: string;
  kind: string;
  id: number;
  label: string | null;
  desc_hex: string | null;
  raw_hex: string;
}

export interface GenResult {
  id: number;
  kind: string;
  detail: string;
  label_written: boolean;
  message: string;
  /** SPKI DER hex captured from the GAK response (RSA/EC only, else null). */
  spki_hex: string | null;
}

export interface KeyDetails {
  id: number;
  label: string | null;
  files: string[];
  key_type: string;
  /** How key_type was derived: cvc (authoritative) | heuristic (size sets) | none. */
  type_source: string;
  size_bits: number | null;
  curve: string | null;
  /** Static PKCS#15 usage word from the description BIT STRING (not configured purposes). */
  usage: number | null;
}

export interface CertEntry {
  fid: string;
  kind: string;
  id: number;
  label: string | null;
  key_type: string;
  size_bits: number | null;
  curve: string | null;
  /** Blob format: "cvc" | "x509" | "unknown". */
  format: string;
  /** False when no CE file is stored (firmware container generation). */
  has_cert: boolean;
}

export interface DeleteResult {
  id: number;
  deleted: string[];
  missing: string[];
}

export interface DkekStatus {
  domain: number;
  /** Configured share count (0 = no DKEK). */
  total: number;
  /** Shares still missing (0 = complete). */
  remaining: number;
  /** KCV as 16 uppercase hex chars. */
  kcv_hex: string;
  /** True when XKEK bytes trail the KCV. */
  has_xkek: boolean;
}

export interface Uf2Info {
  path: string;
  bytes: number;
  blocks: number;
  family_hex: string;
  /** "RP2040" | "RP2350" | "unknown". */
  mcu: string;
  /** Core of the payload family: "ARM" | "RISC-V" | "unknown". */
  core: string;
  /** SHA-256 of the file (lowercase hex). */
  sha256_hex: string;
  /** Expected hash from a `<file>.sha256[.sum]` sidecar, if present. */
  sidecar_hash: string | null;
}

export interface FlashProgress {
  /** waiting-drive | flashing | waiting-reboot | done. */
  phase: string;
  done_bytes: number;
  total_bytes: number;
}

export interface FlashResult {
  old_version: string;
  new_version: string;
}

function isDeviceError(v: unknown): v is DeviceError {
  return (
    typeof v === "object" &&
    v !== null &&
    "code" in v &&
    typeof (v as DeviceError).code === "string"
  );
}

async function call<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  try {
    return await invoke<T>(cmd, args);
  } catch (e) {
    if (typeof e === "string") throw { code: "InvokeFailed", message: e, hint: "" } satisfies DeviceError;
    if (isDeviceError(e)) throw e;
    throw {
      code: "InvokeFailed",
      message: e instanceof Error ? e.message : String(e),
      hint: "Running outside Tauri (browser preview)? Live device data needs `tauri dev`.",
    } satisfies DeviceError;
  }
}

export const tauriApi = {
  listReaders: () => call<string[]>("list_readers"),
  cardStatus: (reader: string) => call<CardStatus>("card_status", { reader }),
  rtcTime: (reader: string) => call<RtcTime>("get_rtc_time", { reader }),
  setRtc: (reader: string, dt: HostDatetime) => call<string>("set_rtc_time", { reader, dt }),
  login: (reader: string, pin: string) => call<string>("login", { reader, pin }),
  changePin: (reader: string, pinRef: number, oldPin: string, newPin: string) =>
    call<string>("change_pin", { reader, pinRef, oldPin, newPin }),
  unblockPin: (reader: string, sopin: string, newPin: string) =>
    call<string>("unblock_pin", { reader, sopin, newPin }),
  dkekStatus: (reader: string, domain: number) =>
    call<DkekStatus>("dkek_status", { reader, domain }),
  dkekImportShare: (reader: string, domain: number, shareHex: string, pin?: string | null) =>
    call<DkekStatus>("dkek_import_share", { reader, domain, shareHex, pin: pin ?? null }),
  dkekSetupDomain: (reader: string, domain: number, shares: number, pin?: string | null) =>
    call<DkekStatus>("dkek_setup_domain", { reader, domain, shares, pin: pin ?? null }),
  wrapKey: (reader: string, id: number, pin?: string | null) =>
    call<string>("wrap_key", { reader, id, pin: pin ?? null }),
  unwrapKey: (reader: string, id: number, blobHex: string, pin?: string | null) =>
    call<string>("unwrap_key", { reader, id, blobHex, pin: pin ?? null }),
  initDevice: (
    reader: string,
    userPin: string,
    soPin: string,
    retries: number,
    dkekSlots: number | null,
    dkekRandom: boolean,
  ) =>
    call<string>("init_device", { reader, userPin, soPin, retries, dkekSlots, dkekRandom }),
  version: (reader: string) => call<FirmwareVersion>("get_version", { reader }),
  pinRetries: (reader: string) => call<PinStatus>("get_pin_retries", { reader }),
  sopinRetries: (reader: string) => call<PinStatus>("get_sopin_retries", { reader }),
  serial: (reader: string) => call<SerialInfo>("get_serial", { reader }),
  platform: (reader: string) => call<PlatformInfo>("get_platform_info", { reader }),
  flash: (reader: string) => call<FlashInfo>("get_flash_info", { reader }),
  secure: (reader: string) => call<SecureInfo>("get_secure_info", { reader }),
  reboot: (reader: string, bootsel: boolean) =>
    call<string>("reboot_device", { reader, bootsel }),
  transmitHsm: (reader: string, apduHex: string, applet?: string | null) =>
    call<TransmitResult>("transmit_hsm", { reader, apduHex, applet: applet ?? null }),
  setDynops: (reader: string, pressConfirm: boolean, keyCounter: boolean, pin?: string | null) =>
    call<string>("set_dynops", { reader, pressConfirm, keyCounter, pin: pin ?? null }),
  keys: (reader: string) => call<KeyEntry[]>("list_keys", { reader }),
  keyDetails: (reader: string, id: number) => call<KeyDetails>("key_details", { reader, id }),
  certs: (reader: string) => call<CertEntry[]>("list_certs", { reader }),
  deleteCert: (reader: string, fid: string, pin?: string | null) =>
    call<string>("delete_cert", { reader, fidHex: fid, pin: pin ?? null }),
  importCert: (reader: string, id: number, fileBytes: number[], pin?: string | null) =>
    call<string>("import_cert", { reader, id, fileBytes, pin: pin ?? null }),
  downloadCert: (reader: string, fid: string, pin?: string | null) =>
    call<string>("export_cert", { reader, fidHex: fid, pin: pin ?? null }),
  exportPubkey: (reader: string, fid: string, pin?: string | null) =>
    call<string>("export_pubkey", { reader, fidHex: fid, pin: pin ?? null }),
  setLabel: (reader: string, fid: string, label: string, pin?: string | null) =>
    call<string>("set_label", { reader, fidHex: fid, label, pin: pin ?? null }),
  genAes: (reader: string, bits: number, label?: string | null, pin?: string | null) =>
    call<GenResult>("gen_aes", { reader, bits, label: label ?? null, pin: pin ?? null }),
  genRsa: (
    reader: string,
    bits: number,
    label?: string | null,
    pin?: string | null,
    useCounter?: number | null,
    algorithms?: number[] | null,
  ) =>
    call<GenResult>("gen_rsa", {
      reader,
      bits,
      label: label ?? null,
      pin: pin ?? null,
      useCounter: useCounter ?? null,
      algorithms: algorithms ?? null,
    }),
  genEc: (
    reader: string,
    curve: string,
    label?: string | null,
    pin?: string | null,
    useCounter?: number | null,
    algorithms?: number[] | null,
  ) =>
    call<GenResult>("gen_ec", {
      reader,
      curve,
      label: label ?? null,
      pin: pin ?? null,
      useCounter: useCounter ?? null,
      algorithms: algorithms ?? null,
    }),
  deleteKey: (reader: string, id: number, pin?: string | null) =>
    call<DeleteResult>("delete_key", { reader, id, pin: pin ?? null }),
  exportCsr: (
    reader: string,
    id: number,
    spkiHex: string | null,
    subject: { cn: string; o: string; ou: string; c: string },
    pin?: string | null,
  ) =>
    call<string>("export_csr", {
      reader,
      id,
      spkiHex: spkiHex ?? null,
      cn: subject.cn,
      o: subject.o,
      ou: subject.ou,
      c: subject.c,
      pin: pin ?? null,
    }),
  parseUf2: (path: string) => call<Uf2Info>("parse_uf2_file", { path }),
  verifyUf2: (path: string, expectedHex: string) =>
    call<string>("verify_uf2_hash", { path, expectedHex }),
  flashUf2: (reader: string, path: string) =>
    call<FlashResult>("flash_uf2", { reader, path }),
};
