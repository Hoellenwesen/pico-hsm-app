import { useCallback, useEffect, useRef, useState } from "react";
import { Archive, Copy, Dices, Download, KeyRound, RefreshCw, Upload } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/Card";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { InfoRow } from "../components/InfoRow";
import { NA } from "../components/ui/NA";
import { tauriApi, type DeviceError, type DkekStatus, type KeyEntry } from "../lib/tauri";
import { isBundle, normalizeShare, type BackupBundle, type BundleKey } from "../lib/backup";
import { loadGenRecord } from "../lib/genRecords";
import { useLang } from "../lib/i18n/LangContext";
import type { DeviceState } from "../hooks/useDevice";

function hexShare(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("").toUpperCase();
}

export function Backup({ device }: { device: DeviceState }) {
  const { t, terr } = useLang();
  const [domain, setDomain] = useState(0);
  const [status, setStatus] = useState<DkekStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notConfigured, setNotConfigured] = useState(false);
  const [busy, setBusy] = useState(false);
  const [genCount, setGenCount] = useState(3);
  const [freshShares, setFreshShares] = useState<string[] | null>(null);
  const [paste, setPaste] = useState("");
  const [importing, setImporting] = useState(false);
  const [setupCount, setSetupCount] = useState(3);
  const [settingUp, setSettingUp] = useState(false);
  const reader = device.live.reader;
  // Stable method ref: `device` is a fresh object every render — depending on
  // it would reload in a loop (flickering refresh + APDU spam).
  const deviceRef = useRef(device);
  deviceRef.current = device;

  const load = useCallback(async () => {
    if (!reader) {
      setStatus(null);
      setError(null);
      setNotConfigured(false);
      return;
    }
    setBusy(true);
    try {
      setStatus(await deviceRef.current.dkekStatus(domain));
      setError(null);
      setNotConfigured(false);
    } catch (e) {
      const text = terr(e as DeviceError);
      setStatus(null);
      setNotConfigured((e as DeviceError)?.code === "NoDkekDomain");
      setError(text.hint ? `${text.message}. ${text.hint}` : text.message);
    } finally {
      setBusy(false);
    }
  }, [reader, domain, terr]);

  useEffect(() => {
    void load();
  }, [load]);

  function generate() {
    const n = Math.min(8, Math.max(1, genCount));
    setFreshShares(Array.from({ length: n }, hexShare));
  }

  async function copyShare(s: string, i: number) {
    try {
      await navigator.clipboard.writeText(s);
      toast.success(t("bk.copied"), { description: t("bk.copiedHint", { n: i + 1 }) });
    } catch {
      toast.error(t("bk.copyFailed"), { description: t("bk.copyFailedHint") });
    }
  }

  async function runSetup() {
    const n = Math.min(8, Math.max(1, setupCount));
    setSettingUp(true);
    try {
      const res = await device.dkekSetupDomain(domain, n);
      setStatus(res);
      setError(null);
      setNotConfigured(false);
      toast.success(t("bk.domainCreated"), { description: t("bk.domainCreatedHint", { domain, n }) });
    } catch (e) {
      const err = e as DeviceError;
      if (err?.auth_required) {
        toast.info(t("common.loginRequired"), { description: t("common.loginAgain") });
      } else {
        const text = terr(err);
        toast.error(t("bk.setupFailed"), { description: text.hint ? `${text.message}. ${text.hint}` : text.message });
      }
    } finally {
      setSettingUp(false);
    }
  }

  async function runImport() {
    const hex = normalizeShare(paste);
    if (!hex) {
      toast.error(t("bk.invalidShare"), { description: t("bk.invalidShareHint") });
      return;
    }
    setImporting(true);
    try {
      const res = await device.dkekImportShare(domain, hex);
      setStatus(res);
      setPaste("");
      if (res.remaining === 0) {
        toast.success(t("bk.dkekComplete"), { description: t("bk.dkekCompleteHint", { kcv: res.kcv_hex }) });
      } else {
        toast.success(t("bk.shareImported"), { description: t("bk.shareImportedHint", { remaining: res.remaining, total: res.total }) });
      }
    } catch (e) {
      const err = e as DeviceError;
      if (err?.auth_required) {
        toast.info(t("common.loginRequired"), { description: t("common.loginAgain") });
      } else {
        const text = terr(err);
        toast.error(t("bk.importFailed"), { description: text.hint ? `${text.message}. ${text.hint}` : text.message });
      }
    } finally {
      setImporting(false);
    }
  }

  const complete = status !== null && status.total > 0 && status.remaining === 0;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold tracking-tight">{t("bk.title")}</h1>
          <p className="text-sm text-muted-foreground">
            {t("bk.sub")}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-xs text-muted-foreground">
            {t("bk.domain")}{" "}
            <input
              type="number"
              min={0}
              max={15}
              value={domain}
              onChange={(e) => setDomain(Math.min(15, Math.max(0, Number(e.target.value))))}
              className="h-8 w-16 rounded-md border border-border bg-background px-2 text-sm outline-none focus:border-primary"
            />
          </label>
          <Button variant="outline" disabled={!device.online || busy} onClick={() => void load()}>
            <RefreshCw size={15} /> {t("common.refresh")}
          </Button>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>
            <KeyRound size={16} className="text-primary" /> {t("bk.dkekTitle", { domain })}
          </CardTitle>
          <CardDescription>{t("bk.dkekSub")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-0.5">
          {!reader ? (
            <NA />
          ) : notConfigured ? (
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground">
                {t("bk.notConfigured", { domain })}
              </p>
              <div className="flex items-center gap-2">
                <label className="text-xs text-muted-foreground">
                  {t("bk.sharesLabel")}{" "}
                  <input
                    type="number"
                    min={1}
                    max={8}
                    value={setupCount}
                    onChange={(e) => setSetupCount(Math.min(8, Math.max(1, Number(e.target.value))))}
                    className="h-8 w-16 rounded-md border border-border bg-background px-2 text-sm outline-none focus:border-primary"
                  />
                </label>
                <Button variant="primary" disabled={!device.online || settingUp} onClick={() => void runSetup()}>
                  {settingUp ? t("bk.creating") : t("bk.createDomain", { domain })}
                </Button>
              </div>
            </div>
          ) : error ? (
            <p className="text-sm text-red-500">{error}</p>
          ) : status === null ? (
            <p className="text-sm text-muted-foreground">{busy ? t("common.loading") : t("common.none")}</p>
          ) : status.total === 0 ? (
            <p className="text-sm text-muted-foreground">
              {t("bk.noDkek")}
            </p>
          ) : (
            <>
              <InfoRow icon={Archive} label={t("bk.sharesLabel")}>
                {complete ? (
                  <Badge variant="success">
                    {t("bk.sharesDone", { done: status.total - status.remaining, total: status.total })}
                  </Badge>
                ) : (
                  <Badge variant="warning">
                    {t("bk.sharesProgress", { done: status.total - status.remaining, total: status.total })}
                  </Badge>
                )}
              </InfoRow>
              <InfoRow icon={KeyRound} label={t("bk.kcv")}>
                <span className="font-mono text-xs" title={t("bk.kcvTitle")}>
                  {status.kcv_hex}
                </span>
              </InfoRow>
              <p className="pt-1 text-xs text-muted-foreground">
                {t("bk.kcvExplain")}
              </p>
              {status.has_xkek && (
                <p className="pt-1 text-xs text-muted-foreground">{t("bk.xkek")}</p>
              )}
            </>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-5 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>
              <Dices size={16} className="text-primary" /> {t("bk.genTitle")}
            </CardTitle>
            <CardDescription>
              {t("bk.genSub")}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex items-center gap-2">
              <label className="text-xs text-muted-foreground">
                {t("bk.count")}{" "}
                <input
                  type="number"
                  min={1}
                  max={8}
                  value={genCount}
                  onChange={(e) => setGenCount(Math.min(8, Math.max(1, Number(e.target.value))))}
                  className="h-8 w-16 rounded-md border border-border bg-background px-2 text-sm outline-none focus:border-primary"
                />
              </label>
              <Button variant="outline" onClick={generate}>
                <Dices size={15} /> {t("bk.generate")}
              </Button>
              {freshShares && (
                <Button variant="ghost" onClick={() => setFreshShares(null)}>
                  {t("bk.discard")}
                </Button>
              )}
            </div>
            {freshShares && (
              <div className="space-y-2">
                <p className="text-xs font-semibold text-amber-500">
                  {t("bk.secretWarn")}
                </p>
                {freshShares.map((s, i) => (
                  <div key={i} className="flex items-center gap-2 rounded border border-border/50 p-2">
                    <span className="text-xs text-muted-foreground">#{i + 1}</span>
                    <code className="min-w-0 flex-1 break-all font-mono text-[11px]">{s}</code>
                    <button
                      title={t("bk.copyShare", { n: i + 1 })}
                      onClick={() => void copyShare(s, i)}
                      className="rounded p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                    >
                      <Copy size={14} />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>
              <Upload size={16} className="text-primary" /> {t("bk.importTitle")}
            </CardTitle>
            <CardDescription>{t("bk.importSub")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <textarea
              value={paste}
              onChange={(e) => setPaste(e.target.value)}
              spellCheck={false}
              rows={3}
              placeholder={t("bk.sharePh")}
              className="w-full rounded-md border border-border bg-background p-2 font-mono text-xs outline-none focus:border-primary"
            />
            <Button variant="primary" disabled={!device.online || importing} onClick={() => void runImport()}>
              {importing ? t("bk.importing") : t("bk.importBtn")}
            </Button>
          </CardContent>
        </Card>
      </div>

      <WrapCard device={device} domain={domain} kcv={status?.total ? status.kcv_hex : null} />
      <RestoreCard device={device} />
    </div>
  );
}

function WrapCard({ device, domain, kcv }: { device: DeviceState; domain: number; kcv: string | null }) {
  const { t, terr } = useLang();
  const [keys, setKeys] = useState<KeyEntry[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<{ id: number; ok: boolean; text: string }[]>([]);
  const reader = device.live.reader;
  const serial = device.device.serial;

  const loadKeys = useCallback(async () => {
    if (!reader) {
      setKeys(null);
      return;
    }
    try {
      setKeys(await tauriApi.keys(reader));
    } catch {
      setKeys(null);
    }
  }, [reader]);

  useEffect(() => {
    void loadKeys();
  }, [loadKeys]);

  const wrappable = (keys ?? []).filter((k) => k.fid.startsWith("CC") && k.id !== 0);

  async function runBackup() {
    if (!reader || wrappable.length === 0) return;
    if (!kcv) {
      toast.error(t("bk.noDkekToast"), { description: t("bk.noDkekToastHint") });
      return;
    }
    setBusy(true);
    const out: { id: number; ok: boolean; text: string }[] = [];
    const bundleKeys: BundleKey[] = [];
    for (const k of wrappable) {
      try {
        const blobHex = await device.wrapKey(k.id);
        const rec = loadGenRecord(serial, k.id);
        bundleKeys.push({
          id: k.id,
          label: k.label ?? rec?.label ?? null,
          kind: rec?.kind ?? "unknown",
          detail: rec?.detail ?? "",
          blobHex,
        });
        out.push({ id: k.id, ok: true, text: t("bk.wrappedBytes", { n: blobHex.length / 2 }) });
      } catch (e) {
        const err = e as DeviceError;
        if (err?.auth_required) {
          toast.info(t("common.loginRequired"), { description: t("common.loginAgain") });
          out.push({ id: k.id, ok: false, text: t("bk.restoreStopped") });
          break;
        }
        const text = terr(err);
        out.push({ id: k.id, ok: false, text: text.hint ? `${text.message}. ${text.hint}` : text.message });
      }
      setResults(out.map((r) => ({ ...r })));
    }
    setResults(out);
    setBusy(false);
    if (bundleKeys.length > 0) {
      const bundle: BackupBundle = {
        app: "pico-hsm-app",
        format: 1,
        created: new Date().toISOString(),
        boardSerial: serial,
        dkekKcv: kcv,
        domain,
        keys: bundleKeys,
      };
      const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `picohsm-backup-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
      toast.success(t("bk.backupDownloaded"), {
        description: t("bk.backupDownloadedHint", { done: bundleKeys.length, total: wrappable.length }),
      });
    }
    await loadKeys();
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <Download size={16} className="text-primary" /> {t("bk.wrapTitle")}
        </CardTitle>
        <CardDescription>
          {t("bk.wrapSub")}{" "}
          {kcv ? (
            <>
              {t("bk.wrapKcv", { kcv })} <span className="font-mono">{kcv}</span>
            </>
          ) : (
            t("bk.wrapSubNoDkek")
          )}{" "}
          {t("bk.wrapTail")}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {!reader ? (
          <NA />
        ) : (
          <>
            <p className="text-sm text-muted-foreground">
              {wrappable.length === 0 ? t("bk.noKeys") : t("bk.keysToBackup", { count: wrappable.length, ids: wrappable.map((k) => k.id).join(", ") })}
            </p>
            <Button variant="primary" disabled={!device.online || busy || wrappable.length === 0} onClick={() => void runBackup()}>
              {busy ? t("bk.wrapping") : t("bk.backupBtn")}
            </Button>
            {results.length > 0 && (
              <ul className="space-y-1 text-sm">
                {results.map((r) => (
                  <li key={r.id} className="flex items-center justify-between gap-3">
                    <span className="font-mono">ID {r.id}</span>
                    {r.ok ? (
                      <Badge variant="success">{t("bk.wrapped")}</Badge>
                    ) : (
                      <Badge variant="destructive" title={r.text}>
                        {t("bk.skipped")}
                      </Badge>
                    )}
                  </li>
                ))}
              </ul>
            )}
            {results.some((r) => !r.ok) && (
              <p className="text-xs text-muted-foreground">
                {results.filter((r) => !r.ok).map((r) => `ID ${r.id}: ${r.text}`).join(" · ")}
              </p>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

function RestoreCard({ device }: { device: DeviceState }) {
  const { t, terr } = useLang();
  const [bundle, setBundle] = useState<BackupBundle | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [results, setResults] = useState<{ id: number; ok: boolean; text: string }[]>([]);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const reader = device.live.reader;

  async function onFile(file: File | undefined) {
    if (!file) return;
    // Cap before reading: a backup bundle is kilobytes; anything bigger is
    // not ours (protects against accidental multi-GB picks).
    if (file.size > 5 * 1024 * 1024) {
      toast.error(t("bk.fileTooLarge"), { description: t("bk.fileTooLargeHint") });
      return;
    }
    try {
      const parsed: unknown = JSON.parse(await file.text());
      if (!isBundle(parsed)) {
        toast.error(t("bk.invalidBackup"), { description: t("bk.invalidBackupHint") });
        return;
      }
      setBundle(parsed);
      setResults([]);
    } catch {
      toast.error(t("bk.invalidBackup"), { description: t("bk.invalidJson") });
    }
  }

  async function runRestore() {
    if (!bundle || !reader) return;
    setConfirming(false);
    setBusy(true);
    const out: { id: number; ok: boolean; text: string }[] = [];
    let current: KeyEntry[] = [];
    try {
      current = await tauriApi.keys(reader);
    } catch {
      current = [];
    }
    const occupied = new Set(current.filter((k) => k.fid.startsWith("CC")).map((k) => k.id));
    for (const k of bundle.keys) {
      if (occupied.has(k.id)) {
        out.push({ id: k.id, ok: false, text: t("bk.occupiedSkip") });
        continue;
      }
      try {
        const msg = await device.unwrapKey(k.id, k.blobHex);
        out.push({ id: k.id, ok: true, text: msg });
        occupied.add(k.id);
      } catch (e) {
        const err = e as DeviceError;
        if (err?.auth_required) {
          toast.info(t("common.loginRequired"), { description: t("common.loginAgain") });
          out.push({ id: k.id, ok: false, text: t("bk.restoreStopped") });
          break;
        }
        const text = terr(err);
        out.push({ id: k.id, ok: false, text: text.hint ? `${text.message}. ${text.hint}` : text.message });
      }
      setResults(out.map((r) => ({ ...r })));
    }
    setResults(out);
    setBusy(false);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <Upload size={16} className="text-primary" /> {t("bk.restoreTitle")}
        </CardTitle>
        <CardDescription>{t("bk.restoreSub")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <input
          ref={fileRef}
          type="file"
          accept=".json,application/json"
          className="hidden"
          onChange={(e) => void onFile(e.target.files?.[0])}
        />
        <Button
          variant="outline"
          disabled={!device.online || busy}
          onClick={() => {
            if (fileRef.current) fileRef.current.value = "";
            fileRef.current?.click();
          }}
        >
          <Upload size={15} /> {t("bk.chooseFile")}
        </Button>
        {bundle && (
          <div className="space-y-2 text-sm">
            <p className="text-muted-foreground">
              {t("bk.bundleMeta", { count: bundle.keys.length, date: bundle.created.slice(0, 10) })}
              {bundle.boardSerial ? ` ${t("bk.bundleBoard", { serial: bundle.boardSerial.slice(-8) })}` : ""} · DKEK KCV{" "}
              <span className="font-mono text-xs">{bundle.dkekKcv}</span>
            </p>
            <p className="text-xs text-amber-500">
              {t("bk.restoreHint")}
            </p>
            <ul className="space-y-1">
              {bundle.keys.map((k) => {
                const r = results.find((x) => x.id === k.id);
                return (
                  <li key={k.id} className="flex items-center justify-between gap-3">
                    <span className="font-mono">
                      ID {k.id} <span className="font-sans text-muted-foreground">{k.label ?? k.kind}</span>
                    </span>
                    {!r ? (
                      <span className="text-xs text-muted-foreground">{t("bk.restorePending")}</span>
                    ) : r.ok ? (
                      <Badge variant="success">{t("bk.restored")}</Badge>
                    ) : (
                      <Badge variant="destructive" title={r.text}>
                        {t("bk.skipped")}
                      </Badge>
                    )}
                  </li>
                );
              })}
            </ul>
            <Button variant="primary" disabled={!device.online || busy} onClick={() => setConfirming(true)}>
              {busy ? t("bk.restoring") : t("bk.restoreBtn", { count: bundle.keys.length })}
            </Button>
            {results.some((r) => !r.ok) && (
              <p className="text-xs text-muted-foreground">
                {results.filter((r) => !r.ok).map((r) => `ID ${r.id}: ${r.text}`).join(" · ")}
              </p>
            )}
          </div>
        )}
      </CardContent>

      {confirming && bundle && (
        <ConfirmDialog
          title={t("bk.restoreConfirmTitle", { count: bundle.keys.length })}
          description={t("bk.restoreConfirmDesc")}
          confirmLabel={t("bk.restoreConfirmBtn")}
          danger
          busy={busy}
          onConfirm={() => void runRestore()}
          onCancel={() => {
            if (!busy) setConfirming(false);
          }}
        />
      )}
    </Card>
  );
}
