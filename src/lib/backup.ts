/**
 * Backup bundle helpers (pure, unit-tested). A bundle is an UNTRUSTED file
 * until validated — `isBundle` is the gate before any restore touch.
 */

export interface BundleKey {
  id: number;
  label: string | null;
  kind: string;
  detail: string;
  blobHex: string;
}

export interface BackupBundle {
  app: string;
  format: number;
  created: string;
  boardSerial: string | null;
  dkekKcv: string;
  /** UI-selected domain at backup time (informational only). Each key wraps
   * with its OWN domain DKEK; restore matches DKEKs automatically by content,
   * so this field is never used for routing. */
  domain: number;
  keys: BundleKey[];
}

export function isBundle(v: unknown): v is BackupBundle {
  if (typeof v !== "object" || v === null) return false;
  const b = v as BackupBundle;
  return (
    b.app === "pico-hsm-app" &&
    b.format === 1 &&
    typeof b.dkekKcv === "string" &&
    typeof b.domain === "number" &&
    Array.isArray(b.keys) &&
    b.keys.every(
      (k) =>
        typeof k === "object" &&
        k !== null &&
        typeof k.id === "number" &&
        Number.isInteger(k.id) &&
        k.id >= 1 &&
        k.id <= 255 &&
        typeof k.blobHex === "string" &&
        /^[0-9A-Fa-f]+$/.test(k.blobHex) &&
        k.blobHex.length % 2 === 0 &&
        k.blobHex.length > 0,
    )
  );
}

/** Normalize a pasted share: strip whitespace, uppercase. Null when malformed. */
export function normalizeShare(input: string): string | null {
  const hex = input.replace(/\s+/g, "").toUpperCase();
  return /^[0-9A-F]{64}$/.test(hex) ? hex : null;
}
