import { beforeEach, describe, expect, it, vi } from "vitest";
import { audit, auditCount, clearAudit, loadAudit } from "./auditLog";

// Node env has no localStorage: minimal in-memory stub (our logic, not the API).
function stubStorage() {
  const store = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    setItem: (k: string, v: string) => void store.set(k, String(v)),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
  });
}

beforeEach(() => {
  stubStorage();
  clearAudit();
});

describe("audit journal", () => {
  it("appends and reads newest-first", () => {
    audit("key.generate", "ID 3 (RSA-2048)", "ok");
    audit("key.delete", "ID 3", "error", "DeleteFailed");
    const all = loadAudit();
    expect(all).toHaveLength(2);
    expect(all[0].action).toBe("key.delete");
    expect(all[0].code).toBe("DeleteFailed");
    expect(all[1].result).toBe("ok");
    expect(typeof all[0].ts).toBe("string");
  });

  it("caps at 500 (ring buffer)", () => {
    for (let i = 0; i < 520; i++) audit("x", `n${i}`, "ok");
    expect(auditCount()).toBe(500);
    const all = loadAudit();
    expect(all[0].detail).toBe("n519");
  });

  it("survives corrupt storage", () => {
    (localStorage as Storage).setItem("picohsm.auditlog.v1", "[[[broken");
    expect(loadAudit()).toEqual([]);
    expect(auditCount()).toBe(0);
    audit("x", "y", "ok");
    expect(auditCount()).toBe(1);
  });

  it("clears fully", () => {
    audit("x", "y", "ok");
    clearAudit();
    expect(loadAudit()).toEqual([]);
  });

  it("entry shape has no secret fields", () => {
    const e = audit("pin.change", "User-PIN changed", "ok");
    expect(new Set(Object.keys(e))).toEqual(new Set(["ts", "action", "detail", "result"]));
    const err = audit("key.delete", "ID 3", "error", "DeleteFailed");
    expect(new Set(Object.keys(err))).toEqual(new Set(["ts", "action", "detail", "result", "code"]));
  });
});
