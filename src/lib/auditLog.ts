/**
 * Audit journal: mutating app operations (never reads, never secrets).
 * localStorage-backed ring buffer (cap 500), JSON export, clearable.
 *
 * SECRET RULE: entries carry action + ids/labels/counts/versions/result
 * only. Never PINs, shares, blobs, subjects, or file paths (paths may
 * contain user names — sizes only). The unit tests enforce patterns.
 *
 * Documented exceptions (still secret-free):
 * - "csr.export" logs the key id only, although signing consumes a
 *   key-counter step — the counter delta is not observable, only the fact.
 * - "diagnose.raw-apdu" logs applet + INS byte + payload length, never the
 *   payload itself (a VERIFY PIN would fit in there).
 */

export type AuditResult = "ok" | "error";

export interface AuditEntry {
  /** ISO timestamp. */
  ts: string;
  /** Stable action id, e.g. "key.generate". */
  action: string;
  /** Human-safe detail (no secrets, see above). */
  detail: string;
  result: AuditResult;
  /** DeviceError code on failure (no messages — they may echo input). */
  code?: string;
}

const KEY = "picohsm.auditlog.v1";
const CAP = 500;

function loadAll(): AuditEntry[] {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (e): e is AuditEntry =>
        typeof e === "object" &&
        e !== null &&
        typeof (e as AuditEntry).ts === "string" &&
        typeof (e as AuditEntry).action === "string" &&
        typeof (e as AuditEntry).detail === "string" &&
        ((e as AuditEntry).result === "ok" || (e as AuditEntry).result === "error"),
    );
  } catch {
    return [];
  }
}

function storeAll(entries: AuditEntry[]): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(entries));
  } catch {
    // Storage full/blocked — journal is convenience, not critical.
  }
}

/** Append one entry (newest last). Returns the entry. */
export function audit(action: string, detail: string, result: AuditResult, code?: string): AuditEntry {
  const entry: AuditEntry = {
    ts: new Date().toISOString(),
    action,
    detail,
    result,
    ...(code ? { code } : {}),
  };
  const all = loadAll();
  all.push(entry);
  storeAll(all.slice(-CAP));
  return entry;
}

/** Newest first (for display). */
export function loadAudit(): AuditEntry[] {
  return loadAll().reverse();
}

export function clearAudit(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    // ignore
  }
}

export function auditCount(): number {
  return loadAll().length;
}
