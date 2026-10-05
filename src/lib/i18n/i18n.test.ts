import { describe, expect, it } from "vitest";
import { en } from "./en";
import { de } from "./de";
import { fill } from "./LangContext";

describe("i18n catalogs", () => {
  it("de carries exactly the en key sets (errors + ui)", () => {
    expect(new Set(Object.keys(de.errors))).toEqual(new Set(Object.keys(en.errors)));
    expect(new Set(Object.keys(de.ui))).toEqual(new Set(Object.keys(en.ui)));
  });

  it("no empty translations", () => {
    for (const [scope, dict] of [["errors", en.errors], ["ui", en.ui]] as const) {
      for (const [k, v] of Object.entries(dict)) {
        const text = typeof v === "string" ? v : `${v.message}${v.hint}`;
        expect(text.length, `${scope}.${k}`).toBeGreaterThan(0);
      }
    }
    for (const [k, v] of Object.entries(de.errors)) {
      expect(v.message.length, `de.errors.${k}.message`).toBeGreaterThan(0);
      expect(v.hint.length, `de.errors.${k}.hint`).toBeGreaterThan(0);
    }
  });

  it("every error code used by the app exists (spot check of load-bearing codes)", () => {
    const errors = en.errors as Record<string, { message: string; hint: string }>;
    const deErrors = de.errors as Record<string, { message: string; hint: string }>;
    for (const code of [
      "NotInitialized", "WrongPin", "PinBlocked", "ImportVerify", "HashMismatch",
      "StuckBootsel", "CertKeyMismatch", "CounterExhausted", "SignNotAllowed",
      "WrongDkek", "NoDkekDomain", "DomainExists", "FamilyMismatch", "InvokeFailed",
    ]) {
      expect(errors[code], code).toBeDefined();
      expect(deErrors[code], code).toBeDefined();
    }
  });
});

describe("fill", () => {
  it("interpolates vars, keeps unknown placeholders", () => {
    expect(fill("Hi {name}!", { name: "Bo" })).toBe("Hi Bo!");
    expect(fill("Hi {name}!", {})).toBe("Hi {name}!");
    expect(fill("plain")).toBe("plain");
  });
});
