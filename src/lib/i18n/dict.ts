/**
 * i18n types. Dictionaries are flat key -> string maps (dotted key names);
 * `en` is the source of truth, `de` must carry the exact same key set
 * (enforced by tests + `Dict` type).
 */

export type Lang = "en" | "de";

export interface ErrorText {
  message: string;
  hint: string;
}

/** Variables for interpolation: {name} placeholders. */
export type Vars = Record<string, string | number>;

export interface Dict {
  errors: Record<string, ErrorText>;
  ui: Record<string, string>;
}
