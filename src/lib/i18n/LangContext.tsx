import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { DeviceError } from "../tauri";
import type { Lang, Vars } from "./dict";
import { en } from "./en";
import { de } from "./de";

const STORE_KEY = "picohsm.lang";
const catalogs = { en, de } as const;

function detectLang(): Lang {
  try {
    const stored = localStorage.getItem(STORE_KEY);
    if (stored === "en" || stored === "de") return stored;
  } catch {
    // ignore
  }
  try {
    return navigator.language.toLowerCase().startsWith("de") ? "de" : "en";
  } catch {
    return "en";
  }
}

/** Fill {placeholders}; unknown vars stay verbatim (never crash rendering). */
export function fill(template: string, vars?: Vars): string {
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (m, name: string) =>
    vars[name] !== undefined ? String(vars[name]) : m,
  );
}

interface LangCtx {
  lang: Lang;
  setLang: (l: Lang) => void;
  /** UI string by key. Missing key falls back to English, then the key itself. */
  t: (key: string, vars?: Vars) => string;
  /** Plural by count (DE/EN share the one/other rule). */
  tp: (key: string, count: number, vars?: Vars) => string;
  /** Backend error by code. Unknown codes fall back to the server text. */
  terr: (err: DeviceError) => { message: string; hint: string };
}

const Ctx = createContext<LangCtx | null>(null);

export function LangProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(detectLang);

  useEffect(() => {
    try {
      localStorage.setItem(STORE_KEY, lang);
    } catch {
      // ignore
    }
    try {
      document.documentElement.lang = lang;
    } catch {
      // ignore
    }
  }, [lang]);

  const setLang = useCallback((l: Lang) => setLangState(l), []);

  const t = useCallback(
    (key: string, vars?: Vars): string => {
      const dict = catalogs[lang].ui as Record<string, string>;
      const fallback = (catalogs.en.ui as Record<string, string>)[key];
      return fill(dict[key] ?? fallback ?? key, vars);
    },
    [lang],
  );

  const tp = useCallback(
    (key: string, count: number, vars?: Vars): string =>
      t(count === 1 ? `${key}_one` : `${key}_other`, { ...vars, count }),
    [t],
  );

  const terr = useCallback(
    (err: DeviceError): { message: string; hint: string } => {
      const entry = (catalogs[lang].errors as Record<string, { message: string; hint: string }>)[err.code];
      if (entry) return { ...entry };
      const fallback = (catalogs.en.errors as Record<string, { message: string; hint: string }>)[err.code];
      if (fallback) return { ...fallback };
      return { message: err.message || err.code || String(err), hint: err.hint || "" };
    },
    [lang],
  );

  const value = useMemo(() => ({ lang, setLang, t, tp, terr }), [lang, setLang, t, tp, terr]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useLang(): LangCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useLang outside LangProvider");
  return ctx;
}
