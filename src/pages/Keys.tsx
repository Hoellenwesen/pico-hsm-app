import { useCallback, useEffect, useMemo, useState } from "react";
import { FileSignature, Info, KeyRound, Pencil, Plus, RefreshCw, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/Card";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { NA } from "../components/ui/NA";
import { ModalCancel, ModalShell } from "../components/ui/ModalShell";
import { useLang } from "../lib/i18n/LangContext";
import { deleteGenRecord, loadGenRecord, patchGenRecord, saveGenRecord } from "../lib/genRecords";
import { tauriApi, type DeviceError, type KeyEntry } from "../lib/tauri";
import type { DeviceState } from "../hooks/useDevice";

function KindBadge({ kind, fid }: { kind: string; fid: string }) {
  const { t } = useLang();
  switch (kind) {
    case "key":
      return (
        <Badge variant="info" title={t("keys.badgeKey", { fid })}>
          {fid}
        </Badge>
      );
    case "prkd":
      return (
        <Badge variant="outline" title={t("keys.badgePrkd", { fid })}>
          {fid}
        </Badge>
      );
    case "cert":
      return (
        <Badge variant="success" title={t("keys.badgeCert", { fid })}>
          {fid}
        </Badge>
      );
    default:
      return (
        <Badge variant="warning" title={t("keys.badgeUnknown", { fid })}>
          {fid}
        </Badge>
      );
  }
}

/** Purpose category id -> display label (shared by generate + details). */
function purposeLabel(t: (k: string) => string, id: string | null): string | null {
  if (id === null) return null;
  switch (id) {
    case "default":
      return t("keys.purposeDefault");
    case "sign":
      return t("keys.purposeSign");
    case "decrypt":
      return t("keys.purposeDecrypt");
    case "derive":
      return t("keys.purposeDerive");
    case "wrap":
      return t("keys.purposeWrap");
    case "custom":
      return t("keys.purposeCustom");
    default:
      return id;
  }
}

interface KeyGroup {
  id: number;
  label: string | null;
  files: KeyEntry[];
  isDevice: boolean;
  /** Description record to patch when renaming (C4/C8/C9), if any. */
  labelFid: string | null;
  /** Raw bytes of that description record (details popup + field mapping). */
  descHex: string | null;
}

/** Group FID rows by key id: CC (key) + C4 (PRKD) + CE (cert) belong together. */
function groupEntries(entries: KeyEntry[], deviceKeyLabel: string): { groups: KeyGroup[]; singles: KeyEntry[] } {
  const byId = new Map<number, KeyEntry[]>();
  const singles: KeyEntry[] = [];
  for (const e of entries) {
    if (e.fid.startsWith("CC") || e.fid.startsWith("C4") || e.fid.startsWith("CE")) {
      const list = byId.get(e.id) ?? [];
      list.push(e);
      byId.set(e.id, list);
    } else {
      singles.push(e);
    }
  }
  const groups: KeyGroup[] = [...byId.entries()]
    .map(([id, files]) => {
      const label =
        files.find((f) => f.fid.startsWith("C4"))?.label ??
        files.find((f) => f.label)?.label ??
        (id === 0 ? deviceKeyLabel : null);
      const labelFid =
        files.find((f) => f.fid.startsWith("C4"))?.fid ??
        files.find((f) => f.fid.startsWith("C8") || f.fid.startsWith("C9"))?.fid ??
        null;
      const descHex =
        files.find((f) => f.fid.startsWith("C4"))?.desc_hex ??
        files.find((f) => f.desc_hex)?.desc_hex ??
        null;
      return { id, label, files, isDevice: id === 0, labelFid, descHex };
    })
    .sort((a, b) => a.id - b.id);
  return { groups, singles };
}

const RSA_SIZES = [1024, 2048, 3072, 4096];
const AES_SIZES = [128, 192, 256, 512];
const EC_CURVES = [
  "secp192r1",
  "secp256r1",
  "secp384r1",
  "secp521r1",
  "brainpoolP256r1",
  "brainpoolP384r1",
  "brainpoolP512r1",
  "secp192k1",
  "secp256k1",
  "curve25519",
  "curve448",
  "ed25519",
  "ed448",
];

interface PurposeDef {
  id: string;
  bytes: number[];
}

const RSA_PURPOSES: PurposeDef[] = [
  { id: "default", bytes: [] },
  { id: "sign", bytes: [0x33, 0x43] },
  { id: "decrypt", bytes: [0x22, 0x23] },
  { id: "wrap", bytes: [0x92, 0x93] },
  { id: "custom", bytes: [] },
];

const EC_PURPOSES: PurposeDef[] = [
  { id: "default", bytes: [] },
  { id: "sign", bytes: [0x73] },
  { id: "derive", bytes: [0x80] },
  { id: "wrap", bytes: [0x92, 0x93] },
  { id: "custom", bytes: [] },
];

const RSA_CUSTOM: [number, string][] = [
  [0x33, "PKCS1-SHA256 sign"],
  [0x43, "PSS-SHA256 sign"],
  [0x22, "Decrypt PKCS1"],
  [0x23, "Decrypt OAEP"],
  [0x92, "Wrap"],
  [0x93, "Unwrap"],
];

const EC_CUSTOM: [number, string][] = [
  [0x73, "ECDSA-SHA256 sign"],
  [0x80, "ECDH derive"],
  [0x92, "Wrap"],
  [0x93, "Unwrap"],
];

function GenerateDialog({
  device,
  onClose,
  onDone,
}: {
  device: DeviceState;
  onClose: () => void;
  onDone: () => void;
}) {
  const [kind, setKind] = useState<"rsa" | "aes" | "ec">("rsa");
  const [bits, setBits] = useState(2048);
  const [curve, setCurve] = useState("secp256r1");
  const [label, setLabel] = useState("");
  const [confirmedSlow, setConfirmedSlow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [unlimited, setUnlimited] = useState(true);
  const [counter, setCounter] = useState(1000);
  const [purpose, setPurpose] = useState("default");
  const [customSel, setCustomSel] = useState<number[]>([]);
  const { t, terr } = useLang();

  const slow = kind === "rsa" && bits >= 3072;
  const labelOk = label === "" || (label.length <= 64 && !/[\x00-\x1F\x7F]/.test(label));
  const purposes = kind === "rsa" ? RSA_PURPOSES : EC_PURPOSES;
  const canSubmit =
    device.online && !busy && labelOk && (!slow || confirmedSlow) && (purpose !== "custom" || customSel.length > 0);

  async function submit() {
    if (!canSubmit) return;
    setBusy(true);
    try {
      const entry = purposes.find((p) => p.id === purpose);
      const algorithms =
        kind === "aes" || purpose === "default"
          ? null
          : purpose === "custom"
            ? [...customSel]
            : (entry?.bytes ?? null);
      const trimmedLabel = label.trim() === "" ? null : label.trim();
      const useCounter = kind === "aes" || unlimited ? null : counter;
      const res = await device.generateKey(kind, kind === "ec" ? curve : bits, trimmedLabel, {
        counter: useCounter,
        algorithms,
      });
      if (device.device.serial) {
        saveGenRecord(device.device.serial, res.id, {
          kind,
          detail: kind === "ec" ? curve : String(bits),
          label: trimmedLabel,
          counter: useCounter,
          algorithms,
          purpose: kind === "aes" || purpose === "default" ? null : purpose,
          spkiHex: res.spki_hex ?? null,
        });
      }
      toast.success(t("keys.generated"), { description: res.message });
      onDone();
    } catch (e) {
      const err = e as DeviceError;
      if (err?.auth_required) {
        toast.info(t("common.loginRequired"), { description: t("common.loginAgain") });
      } else {
        const text = terr(err);
        toast.error(t("keys.genFailed"), { description: text.hint ? `${text.message}. ${text.hint}` : text.message });
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <ModalShell onCancel={onClose} busy={busy}>
        <CardHeader>
          <CardTitle>
            <Plus size={16} className="text-primary" /> {t("keys.genTitle")}
          </CardTitle>
          <CardDescription>
            {t("keys.genSub", { session: device.unlocked ? t("keys.genUnlocked") : t("keys.genLocked") })}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex gap-2">
            {(["rsa", "ec", "aes"] as const).map((k) => (
              <Button key={k} variant={kind === k ? "primary" : "outline"} disabled={busy} onClick={() => { setKind(k); setBits(k === "rsa" ? 2048 : 256); setConfirmedSlow(false); setPurpose("default"); setCustomSel([]); }}>
                {k.toUpperCase()}
              </Button>
            ))}
          </div>
          {kind === "ec" ? (
            <label className="block space-y-1">
              <span className="text-xs font-medium text-muted-foreground">{t("keys.curveLabel")}</span>
              <select
                value={curve}
                disabled={busy}
                onChange={(e) => setCurve(e.currentTarget.value)}
                className="h-9 w-full rounded-lg border border-border bg-background px-3 text-sm outline-none focus:border-primary"
              >
                {EC_CURVES.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </label>
          ) : (
          <label className="block space-y-1">
            <span className="text-xs font-medium text-muted-foreground">
              {kind === "rsa" ? t("keys.sizeLabel") : t("keys.sizeLabelAes")}
            </span>
            <select
              value={bits}
              disabled={busy}
              onChange={(e) => { setBits(Number(e.currentTarget.value)); setConfirmedSlow(false); }}
              className="h-9 w-full rounded-lg border border-border bg-background px-3 text-sm outline-none focus:border-primary"
            >
              {(kind === "rsa" ? RSA_SIZES : AES_SIZES).map((b) => (
                <option key={b} value={b}>
                  {b}
                </option>
              ))}
            </select>
          </label>
          )}
          {kind !== "aes" ? (
            <>
              <div className="space-y-1">
                <span className="text-xs font-medium text-muted-foreground">{t("keys.usageLimit")}</span>
                <label className="flex cursor-pointer items-center gap-2 text-sm">
                  <input type="checkbox" checked={unlimited} disabled={busy} onChange={(e) => setUnlimited(e.currentTarget.checked)} />
                  {t("keys.unlimited")}
                </label>
                {!unlimited && (
                  <span className="flex items-center gap-2">
                    <input
                      type="range"
                      min={1}
                      max={100000}
                      value={counter}
                      disabled={busy}
                      onChange={(e) => setCounter(Number(e.currentTarget.value))}
                      className="flex-1"
                    />
                    <span className="w-16 text-right font-mono text-sm">{counter}</span>
                  </span>
                )}
                <p className="text-xs text-muted-foreground">
                  {unlimited
                    ? t("keys.noCounter")
                    : t("keys.counterDecrement")}
                </p>
              </div>
              <div className="space-y-1">
                <span className="text-xs font-medium text-muted-foreground">{t("keys.purpose")}</span>
                <div className="flex flex-wrap gap-1">
                  {purposes.map((p) => (
                    <Button
                      key={p.id}
                      variant={purpose === p.id ? "primary" : "outline"}
                      disabled={busy}
                      onClick={() => setPurpose(p.id)}
                      className="h-7 px-2 text-xs"
                    >
                      {purposeLabel(t, p.id)}
                    </Button>
                  ))}
                </div>
                {purpose === "custom" && (
                  <>
                    <div className="flex flex-wrap gap-x-4 gap-y-1 pt-1">
                      {(kind === "rsa" ? RSA_CUSTOM : EC_CUSTOM).map(([algo, name]) => (
                        <label key={algo} className="flex cursor-pointer items-center gap-1.5 text-xs">
                          <input
                            type="checkbox"
                            disabled={busy}
                            checked={customSel.includes(algo)}
                            onChange={(e) =>
                              setCustomSel(
                                e.currentTarget.checked
                                  ? [...customSel, algo]
                                  : customSel.filter((a) => a !== algo),
                              )
                            }
                          />
                          <span className="font-mono text-muted-foreground">0x{algo.toString(16).toUpperCase()}</span> {name}
                        </label>
                      ))}
                    </div>
                    <p className="text-xs text-amber-500">
                      {t("keys.customWarn")}
                    </p>
                  </>
                )}
              </div>
            </>
          ) : (
            <p className="text-xs text-muted-foreground">
              {t("keys.aesUnsupported")}
            </p>
          )}
          {slow && (
            <p className="rounded-lg bg-amber-500/10 p-3 text-xs text-amber-500">
              {t("keys.slowWarn", { bits })}
              <label className="mt-2 flex cursor-pointer items-center gap-2 font-medium">
                <input type="checkbox" checked={confirmedSlow} disabled={busy} onChange={(e) => setConfirmedSlow(e.currentTarget.checked)} />
                {t("keys.slowConfirm")}
              </label>
            </p>
          )}
          <label className="block space-y-1">
            <span className="text-xs font-medium text-muted-foreground">{t("keys.labelLabel")}</span>
            <input
              value={label}
              disabled={busy}
              maxLength={64}
              onChange={(e) => setLabel(e.currentTarget.value)}
              placeholder={t("keys.labelPh")}
              className="h-9 w-full rounded-lg border border-border bg-background px-3 text-sm outline-none focus:border-primary"
            />
          </label>
          {!labelOk && <p className="text-xs text-red-500">{t("keys.labelBad")}</p>}
          <div className="flex gap-2">
            <Button variant="primary" disabled={!canSubmit} onClick={() => void submit()}>
              {busy ? t("keys.generating") : t("keys.generate")}
            </Button>
            <ModalCancel onCancel={onClose} busy={busy} />
          </div>
          {busy && (
            <p className="text-xs text-muted-foreground">
              {t("keys.busyNote")}
            </p>
          )}
          <p className="text-xs text-muted-foreground">
            {t("keys.errorNote")}
          </p>
        </CardContent>
    </ModalShell>
  );
}

function DeleteDialog({
  targets,
  busy,
  results,
  onClose,
  onConfirm,
}: {
  targets: KeyGroup[];
  busy: boolean;
  results: { id: number; label: string | null; ok: boolean; text: string }[];
  onClose: () => void;
  onConfirm: () => void;
}) {
  const { t } = useLang();
  const running = busy || (results.length > 0 && results.length < targets.length);
  const finished = results.length === targets.length && targets.length > 0;
  return (
    <ConfirmDialog
      title={targets.length === 1 ? t("keys.delTitleOne", { id: targets[0].id }) : t("keys.delTitleMany", { count: targets.length })}
      description={t("keys.delDesc")}
      confirmLabel={finished ? t("keys.delClose") : targets.length === 1 ? t("common.delete") : t("keys.delTitleMany", { count: targets.length })}
      danger={!finished}
      busy={busy}
      onConfirm={() => {
        if (finished) onClose();
        else onConfirm();
      }}
      onCancel={onClose}
    >
      <ul className="space-y-1 text-sm">
        {targets.map((g) => {
          const r = results.find((x) => x.id === g.id);
          return (
            <li key={g.id} className="flex items-center justify-between gap-3">
              <span className="font-mono">
                ID {g.id} <span className="font-sans text-muted-foreground">{g.label ?? ""}</span>
              </span>
              {!r ? (
                running ? (
                  <span className="text-xs text-muted-foreground">{t("keys.deleting")}</span>
                ) : (
                  <span className="text-xs text-muted-foreground">{t("keys.pending")}</span>
                )
              ) : r.ok ? (
                <Badge variant="success">{t("keys.deleted")}</Badge>
              ) : (
                <Badge variant="destructive" title={r.text}>
                  {t("keys.failed")}
                </Badge>
              )}
            </li>
          );
        })}
      </ul>
      {results.some((r) => !r.ok) && (
        <p className="text-xs text-muted-foreground">
          {results.filter((r) => !r.ok).map((r) => `ID ${r.id}: ${r.text}`).join(" · ")}
        </p>
      )}
    </ConfirmDialog>
  );
}

function DetailsDialog({
  group,
  reader,
  serial,
  onClose,
}: {
  group: KeyGroup;
  reader: string;
  serial: string | null;
  onClose: () => void;
}) {
  const [details, setDetails] = useState<import("../lib/tauri").KeyDetails | null>(null);
  const [error, setError] = useState<string | null>(null);
  const record = loadGenRecord(serial, group.id);
  const { t, terr } = useLang();

  useEffect(() => {
    let cancelled = false;
    tauriApi
      .keyDetails(reader, group.id)
      .then((d) => {
        if (!cancelled) setDetails(d);
      })
      .catch((e) => {
        if (!cancelled) {
          const text = terr(e as DeviceError);
          setError(text.hint ? `${text.message}. ${text.hint}` : text.message);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [reader, group.id, terr]);

  // Generation record (this computer) wins; device-derived values fill gaps.
  // Mismatch guard, precise: only an AUTHORITATIVE device reading (CE-CVC
  // parse) overrules the record. Heuristic size sets overlap (AES-256 vs
  // 256-bit EC), so heuristic disagreement defers to the record.
  const CURVE_BITS: Record<string, number> = {
    secp192r1: 192, secp256r1: 256, secp384r1: 384, secp521r1: 521, brainpoolP256r1: 256,
    brainpoolP384r1: 384, brainpoolP512r1: 512, secp192k1: 192, secp256k1: 256,
    curve25519: 255, curve448: 448, ed25519: 256, ed448: 456,
  };
  const authoritative = details?.type_source === "cvc";
  const recordMismatch = !!record && !!details && authoritative && record.kind.toUpperCase() !== details.key_type;
  const rec = recordMismatch ? null : record;
  const keyType = rec ? rec.kind.toUpperCase() : (details && details.key_type !== "unknown" ? details.key_type : null);
  const sizeBits =
    rec && rec.kind === "ec"
      ? (CURVE_BITS[rec.detail] ?? details?.size_bits ?? null)
      : rec
        ? Number(rec.detail)
        : (details?.size_bits ?? null);
  const curve = rec && rec.kind === "ec" ? rec.detail : (details?.curve ?? null);
  const counterText =
    rec && rec.kind !== "aes"
      ? rec.counter != null
        ? t("keys.detCounterInitial", { n: rec.counter })
        : t("keys.detCounterUnlimited")
      : null;
  // Category badge only: unrestricted at generation, the chosen category
  // otherwise. No algorithm details (curve-independent anyway —
  // e.g. ECDSA-SHA256 is just the hash function, valid for any curve).
  const purposeBadge: string | null = !rec
    ? null
    : (rec.purpose ? (purposeLabel(t, rec.purpose) ?? rec.purpose) : t("keys.purposeAll"));

  // Static PKCS#15 usage word (OpenSC vocabulary) — NOT the configured
  // GAK-0x91 restrictions (those are write-only). Verified against
  // pkcs15-tool output per layout.
  const USAGE_NAMES: [number, string][] = [
    [0x01, "encrypt"],
    [0x02, "decrypt"],
    [0x04, "sign"],
    [0x08, "signRecover"],
    [0x10, "wrap"],
    [0x20, "unwrap"],
    [0x40, "verify"],
    [0x80, "verifyRecover"],
    [0x100, "derive"],
  ];
  const usageText: string | null = (() => {
    const u = details?.usage;
    if (u == null) return null;
    const known = USAGE_NAMES.filter(([bit]) => (u & bit) !== 0).map(([, name]) => name);
    const rest = u & ~USAGE_NAMES.reduce((acc, [bit]) => acc | bit, 0);
    if (rest !== 0) known.push(`0x${rest.toString(16).toUpperCase()}`);
    return known.length > 0 ? known.join(", ") : null;
  })();

  return (
    <ModalShell onCancel={onClose} wide>
      <div className="max-h-[85vh] overflow-y-auto">
        <CardHeader>
          <CardTitle>
            <Info size={16} className="text-primary" /> {t("keys.detTitle", { id: group.id })}
          </CardTitle>
          <CardDescription>
            {group.label ?? t("keys.detNoLabel")} · {group.files.map((f) => f.fid).join(", ")}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          {rec && (
            <p className="text-xs text-muted-foreground">
              <Badge variant="outline" title={t("keys.detRecordedTitle", { ts: rec.createdAt })}>
                {t("keys.detRecorded")}
              </Badge>
            </p>
          )}
          {recordMismatch && (
            <p className="text-xs text-amber-500">
              {t("keys.detMismatch")}
            </p>
          )}
          <div className="flex items-center justify-between gap-3">
            <span className="text-muted-foreground">{t("keys.detKeyType")}</span>
            {keyType ? (
              <Badge variant="info">{keyType}</Badge>
            ) : !details && !error ? (
              <span className="text-xs text-muted-foreground">{t("keys.detReading")}</span>
            ) : error && !record ? (
              <span className="text-xs text-red-500">{t("keys.detError")}</span>
            ) : (
              <NA />
            )}
          </div>
          <div className="flex items-center justify-between gap-3">
            <span className="text-muted-foreground">{t("keys.detCurve")}</span>
            {curve ?? (!details && !error ? <span className="text-xs text-muted-foreground">{t("keys.detReading")}</span> : error && !record ? <span className="text-xs text-red-500">{t("keys.detError")}</span> : <NA />)}
          </div>
          <div className="flex items-center justify-between gap-3">
            <span className="text-muted-foreground">{t("keys.detSize")}</span>
            {sizeBits != null ? (
              <span className="font-mono">{t("keys.detBits", { bits: sizeBits })}</span>
            ) : !details && !error ? (
              <span className="text-xs text-muted-foreground">{t("keys.detReading")}</span>
            ) : error && !record ? (
              <span className="text-xs text-red-500">{t("keys.detError")}</span>
            ) : (
              <NA />
            )}
          </div>
          <div className="flex items-center justify-between gap-3">
            <span className="text-muted-foreground">{t("keys.detUsage")}</span>
            {usageText ?? (!details && !error ? <span className="text-xs text-muted-foreground">{t("keys.detReading")}</span> : error && !details?.usage ? <span className="text-xs text-red-500">{t("keys.detError")}</span> : <NA />)}
          </div>
          <div className="flex items-center justify-between gap-3">
            <span className="text-muted-foreground">{t("keys.detCounter")}</span>
            {counterText ?? (
              <Badge variant="muted" title={t("keys.detCounterNa")}>
                N/A
              </Badge>
            )}
          </div>
          <div className="flex items-center justify-between gap-3">
            <span className="text-muted-foreground">{t("keys.detPurposes")}</span>
            {purposeBadge ? (
              <Badge variant="outline">{purposeBadge}</Badge>
            ) : (
              <Badge variant="muted" title={t("keys.detPurposesNa")}>
                N/A
              </Badge>
            )}
          </div>
          {error && <p className="text-xs text-red-500">{error}</p>}
          <div className="flex gap-2 pt-1">
            <ModalCancel onCancel={onClose} />
          </div>
        </CardContent>
      </div>
    </ModalShell>
  );
}

function CsrDialog({
  id,
  serial,
  device,
  onClose,
}: {
  id: number;
  serial: string | null;
  device: DeviceState;
  onClose: () => void;
}) {
  const [cn, setCn] = useState("");
  const [o, setO] = useState("");
  const [ou, setOu] = useState("");
  const [c, setC] = useState("");
  const [busy, setBusy] = useState(false);
  const { t, terr } = useLang();
  const printable = (s: string) => s.length <= 64 && !/[\x00-\x1F\x7F]/.test(s);
  const cnOk = cn.trim().length >= 1 && cn.trim().length <= 64 && printable(cn.trim());
  const oOk = o.trim() === "" || (printable(o.trim()) && o.trim().length >= 1);
  const ouOk = ou.trim() === "" || (printable(ou.trim()) && ou.trim().length >= 1);
  const cOk = c.trim() === "" || /^[A-Za-z]{2}$/.test(c.trim());
  const canSubmit = device.online && !busy && cnOk && oOk && ouOk && cOk;

  async function submit() {
    if (!canSubmit) return;
    setBusy(true);
    try {
      const spkiHex = loadGenRecord(serial, id)?.spkiHex ?? null;
      const pem = await device.exportCsr(id, spkiHex, {
        cn: cn.trim(),
        o: o.trim(),
        ou: ou.trim(),
        c: c.trim(),
      });
      const blob = new Blob([pem], { type: "application/x-pem-file" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `key-${id}.csr.pem`;
      a.click();
      URL.revokeObjectURL(url);
      toast.success(t("keys.csrExported"), { description: t("keys.csrExportedHint", { id }) });
      onClose();
    } catch (e) {
      const err = e as DeviceError;
      if (err?.auth_required) {
        toast.info(t("common.loginRequired"), { description: t("common.loginAgain") });
      } else {
        const text = terr(err);
        toast.error(t("keys.csrFailed"), { description: text.hint ? `${text.message}. ${text.hint}` : text.message });
      }
    } finally {
      setBusy(false);
    }
  }

  const inputCls =
    "h-8 w-full rounded-md border border-border bg-background px-2 text-sm outline-none focus:border-primary";
  return (
    <ModalShell onCancel={onClose} busy={busy}>
      <CardHeader>
        <CardTitle>
          <FileSignature size={16} className="text-primary" /> {t("keys.csrDialogTitle", { id })}
        </CardTitle>
        <CardDescription>
          {t("keys.csrDialogSub")}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <label className="block">
          <span className="mb-1 block text-xs text-muted-foreground">{t("keys.csrCn")}</span>
          <input value={cn} maxLength={64} onChange={(e) => setCn(e.target.value)} className={inputCls} placeholder="pico.example" />
          {!cnOk && cn !== "" && <span className="text-xs text-red-500">{t("keys.csrCnBad")}</span>}
        </label>
        <label className="block">
          <span className="mb-1 block text-xs text-muted-foreground">{t("keys.csrO")}</span>
          <input value={o} maxLength={64} onChange={(e) => setO(e.target.value)} className={inputCls} />
        </label>
        <label className="block">
          <span className="mb-1 block text-xs text-muted-foreground">{t("keys.csrOu")}</span>
          <input value={ou} maxLength={64} onChange={(e) => setOu(e.target.value)} className={inputCls} />
        </label>
        <label className="block">
          <span className="mb-1 block text-xs text-muted-foreground">{t("keys.csrC")}</span>
          <input value={c} maxLength={2} onChange={(e) => setC(e.target.value)} className={inputCls} placeholder="DE" />
          {!cOk && <span className="text-xs text-red-500">{t("keys.csrCBad")}</span>}
        </label>
        <div className="flex gap-2 pt-1">
          <Button variant="primary" disabled={!canSubmit} onClick={() => void submit()}>
            {busy ? t("keys.csrSigning") : t("keys.csrExport")}
          </Button>
          <ModalCancel onCancel={onClose} busy={busy} />
        </div>
      </CardContent>
    </ModalShell>
  );
}

export function Keys({ device }: { device: DeviceState }) {
  const { t, terr } = useLang();
  const [entries, setEntries] = useState<KeyEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const reader = device.live.reader;

  const load = useCallback(async () => {
    if (!reader) {
      setEntries(null);
      setError(null);
      return;
    }
    setBusy(true);
    try {
      setEntries(await tauriApi.keys(reader));
      setError(null);
    } catch (e) {
      const text = terr(e as DeviceError);
      setEntries(null);
      setError(text.hint ? `${text.message}. ${text.hint}` : text.message);
    } finally {
      setBusy(false);
    }
  }, [reader, terr]);

  useEffect(() => {
    void load();
  }, [load]);

  const { groups, singles } = useMemo(() => groupEntries(entries ?? [], t("keys.deviceKey")), [entries, t]);
  const [showGen, setShowGen] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editValue, setEditValue] = useState("");
  const [saving, setSaving] = useState(false);
  const [selected, setSelected] = useState<number[]>([]);
  const [deleteTargets, setDeleteTargets] = useState<KeyGroup[] | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteDone, setDeleteDone] = useState<{ id: number; label: string | null; ok: boolean; text: string }[]>([]);
  const [detailsId, setDetailsId] = useState<number | null>(null);
  const [csrId, setCsrId] = useState<number | null>(null);

  // Drop selection of groups that vanished (e.g. after delete + reload).
  useEffect(() => {
    setSelected((sel) => sel.filter((id) => groups.some((g) => g.id === id)));
  }, [groups]);

  const deletable = groups.filter((g) => !g.isDevice);
  const allChecked = deletable.length > 0 && deletable.every((g) => selected.includes(g.id));
  const someChecked = selected.length > 0;

  function toggleSelect(id: number) {
    setSelected((sel) => (sel.includes(id) ? sel.filter((x) => x !== id) : [...sel, id]));
  }

  function toggleAll() {
    setSelected(allChecked ? [] : deletable.map((g) => g.id));
  }

  async function runDelete(targets: KeyGroup[]) {
    setDeleteBusy(true);
    setDeleteDone([]);
    const results: { id: number; label: string | null; ok: boolean; text: string }[] = [];
    for (const g of targets) {
      setDeleteDone(results.map((r) => ({ ...r })));
      try {
        const res = await device.deleteKey(g.id);
        const parts = [`${t("keys.delDeleted")}: ${res.deleted.join(", ") || "—"}`];
        if (res.missing.length > 0) parts.push(`${t("keys.delAbsent")}: ${res.missing.join(", ")}`);
        results.push({ id: g.id, label: g.label, ok: true, text: parts.join(" ") });
        deleteGenRecord(device.device.serial, g.id);
      } catch (e) {
        const err = e as DeviceError;
        if (err?.auth_required) {
          toast.info(t("common.loginRequired"), { description: t("common.loginAgain") });
          results.push({ id: g.id, label: g.label, ok: false, text: t("keys.delStopped") });
          break;
        }
        const text = terr(err);
        results.push({ id: g.id, label: g.label, ok: false, text: text.hint ? `${text.message}. ${text.hint}` : text.message });
        break;
      }
    }
    setDeleteDone(results);
    setDeleteBusy(false);
    setSelected([]);
    await load();
  }

  function closeDelete() {
    setDeleteTargets(null);
    setDeleteDone([]);
    setDeleteBusy(false);
  }

  function startEdit(g: KeyGroup) {
    setEditingId(g.id);
    setEditValue(g.label ?? "");
  }

  async function saveEdit(g: KeyGroup) {
    const value = editValue.trim();
    if (!g.labelFid || value === "" || value.length > 64 || saving) return;
    setSaving(true);
    try {
      const msg = await device.renameLabel(g.labelFid, value);
      toast.success(t("keys.labelUpdated"), { description: msg });
      patchGenRecord(device.device.serial, g.id, { label: value });
      setEditingId(null);
      await load();
    } catch (e) {
      const err = e as DeviceError;
      if (err?.auth_required) {
        toast.info(t("common.loginRequired"), { description: t("common.loginAgain") });
      } else {
        const text = terr(err);
        toast.error(t("keys.renameFailed"), { description: text.hint ? `${text.message}. ${text.hint}` : text.message });
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold tracking-tight">{t("keys.title")}</h1>
          <p className="text-sm text-muted-foreground">
            {t("keys.subtitle")}
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="primary" disabled={!device.online || busy} onClick={() => setShowGen(true)}>
            <Plus size={15} /> {t("keys.generate")}
          </Button>
          <Button
            variant="outline"
            disabled={!device.online || busy || selected.length === 0}
            onClick={() => setDeleteTargets(groups.filter((g) => selected.includes(g.id)))}
            className={selected.length > 0 ? "border-red-500/50 text-red-500 hover:bg-red-500/10" : undefined}
          >
            <Trash2 size={15} /> {selected.length > 0 ? t("keys.deleteSelectedN", { count: selected.length }) : t("keys.deleteSelected")}
          </Button>
          <Button variant="outline" disabled={!device.online || busy} onClick={() => void load()}>
            <RefreshCw size={15} /> {t("keys.refresh")}
          </Button>
        </div>
      </div>

      {deleteTargets !== null && (
        <DeleteDialog
          targets={deleteTargets}
          busy={deleteBusy}
          results={deleteDone}
          onClose={closeDelete}
          onConfirm={() => void runDelete(deleteTargets)}
        />
      )}

      {showGen && (
        <GenerateDialog
          device={device}
          onClose={() => setShowGen(false)}
          onDone={() => {
            setShowGen(false);
            void load();
          }}
        />
      )}

      <Card>
        <CardHeader>
          <CardTitle>
            <KeyRound size={16} className="text-primary" /> {t("keys.title")}
          </CardTitle>
          <CardDescription>
            {reader ? (
              <>
                {t("keys.readerLabel")} <span className="font-mono">{reader}</span>
              </>
            ) : (
              t("keys.noBoard")
            )}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {!reader ? (
            <NA />
          ) : error ? (
            <p className="text-sm text-red-500">{error}</p>
          ) : entries === null ? (
            <p className="text-sm text-muted-foreground">{busy ? t("common.loading") : t("common.none")}</p>
          ) : groups.length === 0 && singles.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {t("keys.noKeys")}
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-xs text-muted-foreground">
                    <th className="w-8 py-2 pr-2">
                      <input
                        type="checkbox"
                        title={allChecked ? t("keys.deselectAll") : t("keys.selectAll")}
                        checked={allChecked}
                        ref={(el) => {
                          if (el) el.indeterminate = someChecked && !allChecked;
                        }}
                        onChange={toggleAll}
                        className="h-4 w-4 accent-current"
                      />
                    </th>
                    <th className="py-2 pr-4 font-medium">{t("keys.colId")}</th>
                    <th className="py-2 pr-4 font-medium">{t("keys.colLabel")}</th>
                    <th className="py-2 pr-4 font-medium">{t("keys.colFiles")}</th>
                    <th className="py-2 pr-4 font-medium">{t("keys.colActions")}</th>
                  </tr>
                </thead>
                <tbody>
                  {groups.map((g) => (
                    <tr key={g.id} className="border-b border-border/50 last:border-0 hover:bg-muted/40">
                      <td className="py-2 pr-2">
                        {!g.isDevice && (
                          <input
                            type="checkbox"
                            title={t("keys.selectId", { id: g.id })}
                            checked={selected.includes(g.id)}
                            onChange={() => toggleSelect(g.id)}
                            className="h-4 w-4 accent-current"
                          />
                        )}
                      </td>
                      <td className="py-2 pr-4 font-mono">
                        {g.id}
                        {g.isDevice && (
                          <span className="ml-2" title={t("keys.systemTitle")}>
                            <Badge variant="outline">{t("keys.systemBadge")}</Badge>
                          </span>
                        )}
                      </td>
                      <td className="py-2 pr-4">
                        {editingId === g.id ? (
                          <span className="flex items-center gap-1">
                            <input
                              autoFocus
                              value={editValue}
                              maxLength={64}
                              onChange={(e) => setEditValue(e.currentTarget.value)}
                              onKeyDown={(e) => {
                                if (e.key === "Enter") void saveEdit(g);
                                if (e.key === "Escape") setEditingId(null);
                              }}
                              className="h-7 w-40 rounded-md border border-border bg-background px-2 text-sm outline-none focus:border-primary"
                            />
                            <Button variant="primary" disabled={saving} onClick={() => void saveEdit(g)} className="h-7 px-2 text-xs">
                              {t("common.save")}
                            </Button>
                            <Button variant="ghost" onClick={() => setEditingId(null)} className="h-7 px-2 text-xs">
                              {t("common.cancel")}
                            </Button>
                          </span>
                        ) : (
                          g.label ?? <NA />
                        )}
                      </td>
                      <td className="py-2 pr-4">
                        <span className="flex flex-wrap gap-1">
                          {g.files.map((f) => (
                            <span key={f.fid}><KindBadge kind={f.kind} fid={f.fid} /></span>
                          ))}
                        </span>
                      </td>
                      <td className="py-2 pr-4">
                        <span className="flex items-center gap-1">
                          <button
                            title={t("keys.detailsTitle", { id: g.id })}
                            onClick={() => setDetailsId(g.id)}
                            className="rounded p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                          >
                            <Info size={14} />
                          </button>
                          {!g.isDevice && g.labelFid && editingId !== g.id && (
                            <button
                              title={t("keys.renameTitle", { fid: g.labelFid })}
                              onClick={() => startEdit(g)}
                              className="rounded p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                            >
                              <Pencil size={14} />
                            </button>
                          )}
                          {!g.isDevice && (
                            <button
                              title={t("keys.deleteTitle", { id: g.id })}
                              onClick={() => setDeleteTargets([g])}
                              className="rounded p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                            >
                              <Trash2 size={14} />
                            </button>
                          )}
                          {!g.isDevice && (
                            <button
                              title={t("keys.csrTitle", { id: g.id })}
                              onClick={() => setCsrId(g.id)}
                              className="rounded p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                            >
                              <FileSignature size={14} />
                            </button>
                          )}
                        </span>
                      </td>
                    </tr>
                  ))}
                  {singles.map((k) => (
                    <tr key={k.fid} className="border-b border-border/50 last:border-0 hover:bg-muted/40">
                      <td className="py-2 pr-2" />
                      <td className="py-2 pr-4 font-mono">{k.id}</td>
                      <td className="py-2 pr-4">{k.label ?? <NA />}</td>
                      <td className="py-2 pr-4">
                        <span className="flex flex-wrap gap-1"><KindBadge kind={k.kind} fid={k.fid} /></span>
                      </td>
                      <td className="py-2 pr-4" />
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {detailsId !== null &&
        (() => {
          const g = groups.find((x) => x.id === detailsId);
          if (!g || !device.live.reader) return null;
          return <DetailsDialog group={g} reader={device.live.reader} serial={device.device.serial} onClose={() => setDetailsId(null)} />;
        })()}
      {csrId !== null && (
        <CsrDialog id={csrId} serial={device.device.serial} device={device} onClose={() => setCsrId(null)} />
      )}
    </div>
  );
}
