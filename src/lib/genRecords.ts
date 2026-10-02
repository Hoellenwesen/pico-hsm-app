/**
 * Generation records: what THIS app requested at key generation
 * (kind, size/curve, label, counter limit, purposes). Stored in
 * localStorage keyed by device serial + key id. Display data only —
 * never secrets. Keys created elsewhere (OpenSC, other machines)
 * have no record; the dialog falls back to device-derived values.
 */

export interface GenRecord {
  kind: "aes" | "rsa" | "ec";
  /** Bits (AES/RSA) or curve name (EC). */
  detail: string;
  label: string | null;
  /** Initial usage limit (null = unlimited). NOT a live counter value. */
  counter: number | null;
  /** Algorithm bytes requested (null = default/unrestricted). */
  algorithms: number[] | null;
  /** Purpose category chosen in the dialog (null = default/AES). */
  purpose: string | null;
  /** SPKI DER hex captured at generation (RSA/EC only). Public key — safe to store. */
  spkiHex?: string | null;
  createdAt: string;
}

const PREFIX = "picohsm.genrecord.";

function key(serial: string, id: number): string {
  return `${PREFIX}${serial}.${id}`;
}

export function saveGenRecord(
  serial: string,
  id: number,
  record: Omit<GenRecord, "createdAt">,
): void {
  try {
    localStorage.setItem(key(serial, id), JSON.stringify({ ...record, createdAt: new Date().toISOString() }));
  } catch {
    // Storage full/blocked — records are a convenience, not critical.
  }
}

/** Update fields of an existing record (e.g. label after rename). No-op when absent. */
export function patchGenRecord(serial: string | null, id: number, patch: Partial<GenRecord>): void {
  if (!serial) return;
  const current = loadGenRecord(serial, id);
  if (!current) return;
  try {
    localStorage.setItem(key(serial, id), JSON.stringify({ ...current, ...patch }));
  } catch {
    // ignore
  }
}

export function loadGenRecord(serial: string | null, id: number): GenRecord | null {
  if (!serial) return null;
  try {
    const raw = localStorage.getItem(key(serial, id));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as GenRecord;
    if (typeof parsed.kind !== "string" || typeof parsed.detail !== "string") return null;
    return parsed;
  } catch {
    return null;
  }
}

export function deleteGenRecord(serial: string | null, id: number): void {
  if (!serial) return;
  try {
    localStorage.removeItem(key(serial, id));
  } catch {
    // ignore
  }
}
