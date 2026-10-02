import { describe, expect, it } from "vitest";
import { isBundle, normalizeShare, type BackupBundle } from "./backup";

function validBundle(): BackupBundle {
  return {
    app: "pico-hsm-app",
    format: 1,
    created: new Date().toISOString(),
    boardSerial: "ABC123",
    dkekKcv: "AABBCCDDEEFF0011",
    domain: 0,
    keys: [{ id: 1, label: "test", kind: "rsa", detail: "2048", blobHex: "DEADBEEF" }],
  };
}

describe("isBundle", () => {
  it("accepts a valid bundle and round-trips through JSON", () => {
    const b = validBundle();
    const back: unknown = JSON.parse(JSON.stringify(b));
    expect(isBundle(back)).toBe(true);
  });

  it("rejects wrong app, format, and missing keys", () => {
    const b = validBundle();
    expect(isBundle({ ...b, app: "other" })).toBe(false);
    expect(isBundle({ ...b, format: 2 })).toBe(false);
    expect(isBundle({ ...b, keys: "nope" })).toBe(false);
    expect(isBundle(null)).toBe(false);
    expect(isBundle("string")).toBe(false);
    expect(isBundle([])).toBe(false);
  });

  it("rejects corrupt entries", () => {
    const b = validBundle();
    expect(isBundle({ ...b, keys: [{ ...b.keys[0], id: 0 }] })).toBe(false);
    expect(isBundle({ ...b, keys: [{ ...b.keys[0], id: 256 }] })).toBe(false);
    expect(isBundle({ ...b, keys: [{ ...b.keys[0], blobHex: "" }] })).toBe(false);
    expect(isBundle({ ...b, keys: [{ ...b.keys[0], blobHex: "ZZ" }] })).toBe(false);
    expect(isBundle({ ...b, keys: [{ ...b.keys[0], blobHex: "ABC" }] })).toBe(false);
    expect(isBundle({ ...b, keys: [null] })).toBe(false);
  });
});

describe("normalizeShare", () => {
  it("accepts 64 hex, strips whitespace, uppercases", () => {
    const hex = "ab".repeat(32);
    expect(normalizeShare(hex)).toBe(hex.toUpperCase());
    expect(normalizeShare(`  ${hex.slice(0, 32)}\n${hex.slice(32)}  `)).toBe(hex.toUpperCase());
  });

  it("rejects malformed input", () => {
    expect(normalizeShare("")).toBeNull();
    expect(normalizeShare("ab".repeat(31))).toBeNull();
    expect(normalizeShare("ab".repeat(33))).toBeNull();
    expect(normalizeShare("zz".repeat(32))).toBeNull();
  });
});
