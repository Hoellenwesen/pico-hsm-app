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
import type { DeviceState } from "../hooks/useDevice";

function hexShare(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("").toUpperCase();
}

async function copyText(text: string, label: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success("Copied", { description: label });
  } catch {
    toast.error("Copy failed", { description: "Select the text manually." });
  }
}

export function Backup({ device }: { device: DeviceState }) {
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
      const err = e as DeviceError;
      setStatus(null);
      setNotConfigured(err?.code === "NoDkekDomain");
      setError(err.hint ? `${err.message}. ${err.hint}` : err.message || String(e));
    } finally {
      setBusy(false);
    }
  }, [reader, domain]);

  useEffect(() => {
    void load();
  }, [load]);

  function generate() {
    const n = Math.min(8, Math.max(1, genCount));
    setFreshShares(Array.from({ length: n }, hexShare));
  }

  async function runSetup() {
    const n = Math.min(8, Math.max(1, setupCount));
    setSettingUp(true);
    try {
      const res = await device.dkekSetupDomain(domain, n);
      setStatus(res);
      setError(null);
      setNotConfigured(false);
      toast.success("Domain created", { description: `Domain ${domain} waits for ${n} share(s).` });
    } catch (e) {
      const err = e as DeviceError;
      if (err?.auth_required) {
        toast.info("Login required", { description: "Enter the User-PIN, then create again." });
      } else {
        toast.error("Setup failed", { description: err.hint ? `${err.message}. ${err.hint}` : err.message });
      }
    } finally {
      setSettingUp(false);
    }
  }

  async function runImport() {
    const hex = normalizeShare(paste);
    if (!hex) {
      toast.error("Invalid share", { description: "A share is exactly 64 hex characters (32 bytes)." });
      return;
    }
    setImporting(true);
    try {
      const res = await device.dkekImportShare(domain, hex);
      setStatus(res);
      setPaste("");
      if (res.remaining === 0) {
        toast.success("DKEK complete", { description: `KCV ${res.kcv_hex}. Compare it across boards.` });
      } else {
        toast.success("Share imported", { description: `${res.remaining} of ${res.total} still missing.` });
      }
    } catch (e) {
      const err = e as DeviceError;
      if (err?.auth_required) {
        toast.info("Login required", { description: "Enter the User-PIN, then import again." });
      } else {
        toast.error("Import failed", { description: err.hint ? `${err.message}. ${err.hint}` : err.message });
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
          <h1 className="text-xl font-bold tracking-tight">Backup &amp; Restore</h1>
          <p className="text-sm text-muted-foreground">
            DKEK shares (XOR N-of-N) plus wrapped-key backup files.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-xs text-muted-foreground">
            Domain{" "}
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
            <RefreshCw size={15} /> Refresh
          </Button>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>
            <KeyRound size={16} className="text-primary" /> DKEK domain {domain}
          </CardTitle>
          <CardDescription>Share status and key check value (KCV) — the KCV never exposes the key.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-0.5">
          {!reader ? (
            <NA />
          ) : notConfigured ? (
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground">
                Domain {domain} does not exist yet. Create it with a share count — domain 0
                usually comes from initialization, 1–15 are yours to define. Fresh keys always
                live in domain 0; restore matches each entry to a local DKEK automatically.
              </p>
              <div className="flex items-center gap-2">
                <label className="text-xs text-muted-foreground">
                  Shares{" "}
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
                  {settingUp ? "Creating…" : `Create domain ${domain}`}
                </Button>
              </div>
            </div>
          ) : error ? (
            <p className="text-sm text-red-500">{error}</p>
          ) : status === null ? (
            <p className="text-sm text-muted-foreground">{busy ? "Reading…" : "—"}</p>
          ) : status.total === 0 ? (
            <p className="text-sm text-muted-foreground">
              No DKEK configured for this domain. Initialize the device with DKEK support to use key backup.
            </p>
          ) : (
            <>
              <InfoRow icon={Archive} label="Shares">
                {complete ? (
                  <Badge variant="success">
                    {status.total - status.remaining} / {status.total} complete
                  </Badge>
                ) : (
                  <Badge variant="warning">
                    {status.total - status.remaining} / {status.total} imported
                  </Badge>
                )}
              </InfoRow>
              <InfoRow icon={KeyRound} label="KCV">
                <span className="font-mono text-xs" title="Key check value: fingerprint of the DKEK. Same KCV on two boards or people means the same key — without revealing it.">
                  {status.kcv_hex}
                </span>
              </InfoRow>
              <p className="pt-1 text-xs text-muted-foreground">
                KCV = first 8 bytes of SHA-256 over the DKEK. Compare it across boards and people
                to prove the same key — it never exposes the key itself.
              </p>
              {status.has_xkek && (
                <p className="pt-1 text-xs text-muted-foreground">XKEK material present on this domain.</p>
              )}
            </>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-5 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>
              <Dices size={16} className="text-primary" /> Generate shares
            </CardTitle>
            <CardDescription>
              Shown once — distribute immediately. A lost share means a lost DKEK (XOR N-of-N).
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex items-center gap-2">
              <label className="text-xs text-muted-foreground">
                Count{" "}
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
                <Dices size={15} /> Generate
              </Button>
              {freshShares && (
                <Button variant="ghost" onClick={() => setFreshShares(null)}>
                  Discard from screen
                </Button>
              )}
            </div>
            {freshShares && (
              <div className="space-y-2">
                <p className="text-xs font-semibold text-amber-500">
                  Secret! Store each share separately (print, USB stick, safe). Never all in one place.
                </p>
                {freshShares.map((s, i) => (
                  <div key={i} className="flex items-center gap-2 rounded border border-border/50 p-2">
                    <span className="text-xs text-muted-foreground">#{i + 1}</span>
                    <code className="min-w-0 flex-1 break-all font-mono text-[11px]">{s}</code>
                    <button
                      title={`Copy share #${i + 1}`}
                      onClick={() => void copyText(s, `Share #${i + 1} copied.`)}
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
              <Upload size={16} className="text-primary" /> Import share
            </CardTitle>
            <CardDescription>Paste one 64-hex share. Order does not matter.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <textarea
              value={paste}
              onChange={(e) => setPaste(e.target.value)}
              spellCheck={false}
              rows={3}
              placeholder="64 hex characters…"
              className="w-full rounded-md border border-border bg-background p-2 font-mono text-xs outline-none focus:border-primary"
            />
            <Button variant="primary" disabled={!device.online || importing} onClick={() => void runImport()}>
              {importing ? "Importing…" : "Import share"}
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
      toast.error("No DKEK", { description: "Configure and complete a DKEK domain first." });
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
        out.push({ id: k.id, ok: true, text: `${blobHex.length / 2} bytes wrapped` });
      } catch (e) {
        const err = e as DeviceError;
        if (err?.auth_required) {
          toast.info("Login required", { description: "Enter the User-PIN, then back up again." });
          out.push({ id: k.id, ok: false, text: "Stopped: login required." });
          break;
        }
        out.push({ id: k.id, ok: false, text: err.hint ? `${err.message}. ${err.hint}` : err.message });
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
      toast.success("Backup downloaded", {
        description: `${bundleKeys.length} of ${wrappable.length} keys wrapped. Keep it with the DKEK shares.`,
      });
    }
    await loadKeys();
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <Download size={16} className="text-primary" /> Wrapped-key backup
        </CardTitle>
        <CardDescription>
          Wraps each key with its own domain DKEK into a versioned JSON bundle. Needs WRAP purpose
          per key
          {kcv ? (
            <>
              {" "}· selected domain KCV <span className="font-mono">{kcv}</span>
            </>
          ) : (
            " · no DKEK configured"
          )}
          . Fresh keys always live in domain 0.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {!reader ? (
          <NA />
        ) : (
          <>
            <p className="text-sm text-muted-foreground">
              {wrappable.length === 0 ? "No user keys to back up." : `${wrappable.length} key(s): ${wrappable.map((k) => k.id).join(", ")}`}
            </p>
            <Button variant="primary" disabled={!device.online || busy || wrappable.length === 0} onClick={() => void runBackup()}>
              {busy ? "Wrapping…" : "Back up keys"}
            </Button>
            {results.length > 0 && (
              <ul className="space-y-1 text-sm">
                {results.map((r) => (
                  <li key={r.id} className="flex items-center justify-between gap-3">
                    <span className="font-mono">ID {r.id}</span>
                    {r.ok ? (
                      <Badge variant="success">Wrapped</Badge>
                    ) : (
                      <Badge variant="destructive" title={r.text}>
                        Skipped
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
      toast.error("File too large", { description: "Backup files are small JSON — picked the wrong file?" });
      return;
    }
    try {
      const parsed: unknown = JSON.parse(await file.text());
      if (!isBundle(parsed)) {
        toast.error("Invalid backup file", { description: "Not a pico-hsm-app backup (format 1)." });
        return;
      }
      setBundle(parsed);
      setResults([]);
    } catch {
      toast.error("Invalid backup file", { description: "File is not valid JSON." });
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
        out.push({ id: k.id, ok: false, text: "Skipped: ID in use — delete it first." });
        continue;
      }
      try {
        const msg = await device.unwrapKey(k.id, k.blobHex);
        out.push({ id: k.id, ok: true, text: msg });
        occupied.add(k.id);
      } catch (e) {
        const err = e as DeviceError;
        if (err?.auth_required) {
          toast.info("Login required", { description: "Enter the User-PIN, then restore again." });
          out.push({ id: k.id, ok: false, text: "Stopped: login required." });
          break;
        }
        out.push({ id: k.id, ok: false, text: err.hint ? `${err.message}. ${err.hint}` : err.message });
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
          <Upload size={16} className="text-primary" /> Restore from backup
        </CardTitle>
        <CardDescription>Unwraps each entry under its original ID. Occupied IDs are skipped, never overwritten.</CardDescription>
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
          <Upload size={15} /> Choose backup file
        </Button>
        {bundle && (
          <div className="space-y-2 text-sm">
            <p className="text-muted-foreground">
              {bundle.keys.length} key(s) from {bundle.created.slice(0, 10)}
              {bundle.boardSerial ? ` (board ${bundle.boardSerial.slice(-8)})` : ""} · DKEK KCV{" "}
              <span className="font-mono text-xs">{bundle.dkekKcv}</span>
            </p>
            <p className="text-xs text-amber-500">
              Import the matching DKEK shares first (KCV must match), or restores fail with WrongDkek.
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
                      <span className="text-xs text-muted-foreground">Pending</span>
                    ) : r.ok ? (
                      <Badge variant="success">Restored</Badge>
                    ) : (
                      <Badge variant="destructive" title={r.text}>
                        Skipped
                      </Badge>
                    )}
                  </li>
                );
              })}
            </ul>
            <Button variant="primary" disabled={!device.online || busy} onClick={() => setConfirming(true)}>
              {busy ? "Restoring…" : `Restore ${bundle.keys.length} keys`}
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
          title={`Restore ${bundle.keys.length} keys?`}
          description="Each entry is unwrapped under its original ID; the device matches each entry to a local DKEK automatically (KCV must match). Occupied IDs are skipped, never overwritten."
          confirmLabel="Restore now"
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
