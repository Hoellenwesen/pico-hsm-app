import { useRef, useState } from "react";
import { Check, FileUp, HardDriveDownload, RefreshCw, X } from "lucide-react";
import { open } from "@tauri-apps/plugin-dialog";
import { toast } from "sonner";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/Card";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { InfoRow } from "../components/InfoRow";
import { NA } from "../components/ui/NA";
import { formatBytes } from "../lib/format";
import { tauriApi, type DeviceError, type FlashProgress, type Uf2Info } from "../lib/tauri";
import { useLang } from "../lib/i18n/LangContext";
import type { DeviceState } from "../hooks/useDevice";

type Phase = "idle" | "ready" | "flashing" | "done";

function CheckRow({ ok, label, detail }: { ok: boolean; label: string; detail: string }) {
  return (
    <div className="flex items-center gap-3 py-1.5 text-sm">
      <span
        className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full ${
          ok ? "bg-green-500/15 text-green-500" : "bg-red-500/15 text-red-500"
        }`}
      >
        {ok ? <Check size={13} /> : <X size={13} />}
      </span>
      <span className="font-medium">{label}</span>
      <span className="ml-auto font-mono text-xs text-muted-foreground">{detail}</span>
    </div>
  );
}

function phaseLabel(t: (k: string) => string, phase: string): string {
  switch (phase) {
    case "waiting-drive":
      return t("fw.phWaitingDrive");
    case "flashing":
      return t("fw.phFlashing");
    case "waiting-reboot":
      return t("fw.phWaitingReboot");
    case "power-cycle":
      return t("fw.phPowerCycle");
    case "done":
      return t("fw.phDone");
    default:
      return phase;
  }
}

export function Firmware({ device }: { device: DeviceState }) {
  const { t, terr, lang } = useLang();
  const d = device.device;
  const [file, setFile] = useState<Uf2Info | null>(null);
  const [picking, setPicking] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [phase, setPhase] = useState<Phase>("idle");
  const [progress, setProgress] = useState<FlashProgress | null>(null);
  const [result, setResult] = useState<{ old: string; current: string } | null>(null);
  const [verifyOn, setVerifyOn] = useState(false);
  const [pasted, setPasted] = useState("");
  const [hashOk, setHashOk] = useState<boolean | null>(null);
  const [hashBusy, setHashBusy] = useState(false);
  const [log, setLog] = useState<string[]>([]);
  const lastPhase = useRef<string>("");

  const boardMcu = d.platform ?? null;
  const boardProduct = d.product ?? null;
  const mcuOk = file !== null && boardMcu !== null && file.mcu === boardMcu;
  const productOk = boardProduct === "HSM";
  const fileOk = file !== null && file.mcu !== "unknown";
  const hashRequired = verifyOn;
  const hashPass = !hashRequired || hashOk === true;
  const canFlash = device.online && fileOk && mcuOk && productOk && hashPass && phase !== "flashing";

  async function checkHash(path: string, expected: string): Promise<boolean> {
    setHashBusy(true);
    try {
      await tauriApi.verifyUf2(path, expected);
      setHashOk(true);
      return true;
    } catch (e) {
      const text = terr(e as DeviceError);
      setHashOk(false);
      toast.error(t("fw.hashMismatch"), {
        description: text.hint ? `${text.message}. ${text.hint}` : text.message,
      });
      return false;
    } finally {
      setHashBusy(false);
    }
  }

  async function pickFile() {
    setPicking(true);
    try {
      const path = await open({
        multiple: false,
        directory: false,
        filters: [{ name: "UF2 firmware", extensions: ["uf2"] }],
      });
      if (typeof path !== "string" || !path) return;
      try {
        const info = await tauriApi.parseUf2(path);
        setFile(info);
        setPhase("ready");
        setResult(null);
        if (info.mcu === "unknown") {
          toast.warning(t("fw.unknownFamily"), { description: t("fw.unknownFamilyHint") });
        }
        // Hash check defaults to the sidecar when present, stays optional.
        const auto = info.sidecar_hash !== null;
        setVerifyOn(auto);
        setPasted("");
        setHashOk(null);
        if (auto && info.sidecar_hash) {
          const ok = await checkHash(info.path, info.sidecar_hash);
          if (ok) toast.success(t("fw.hashVerified"), { description: t("fw.hashVerifiedHint") });
        }
      } catch (e) {
        const text = terr(e as DeviceError);
        setFile(null);
        setPhase("idle");
        toast.error(t("fw.invalidFile"), {
          description: text.hint ? `${text.message}. ${text.hint}` : text.message,
        });
      }
    } finally {
      setPicking(false);
    }
  }

  function stamp(msg: string) {
    const t = new Date().toLocaleTimeString(lang, { hour12: false });
    setLog((l) => [...l.slice(-49), `${t} ${msg}`]);
  }

  function onFlashProgress(p: FlashProgress) {
    setProgress(p);
    if (p.phase !== lastPhase.current) {
      lastPhase.current = p.phase;
      stamp(
        p.phase === "flashing"
          ? t("fw.logCopying")
          : phaseLabel(t, p.phase),
      );
    }
  }

  async function runFlash() {
    if (!file || phase === "flashing") return;
    setConfirming(false);
    setLog([]);
    lastPhase.current = "";
    stamp(t("fw.logStart", { blocks: file.blocks, size: formatBytes(file.bytes) }));
    // Optional hash gate runs BEFORE any reboot — the board stays untouched.
    if (verifyOn) {
      const expected = pasted.trim() !== "" ? pasted.trim() : (file.sidecar_hash ?? "");
      if (expected === "") {
        toast.error(t("fw.noHash"), { description: t("fw.noHashHint") });
        return;
      }
      const ok = await checkHash(file.path, expected);
      if (!ok) return;
    }
    setPhase("flashing");
    setProgress({ phase: "waiting-drive", done_bytes: 0, total_bytes: file.bytes });
    try {
      const res = await device.flashFirmware(file.path, onFlashProgress);
      stamp(t("fw.logBack", { version: res.new_version }));
      setResult({ old: res.old_version, current: res.new_version });
      setPhase("done");
      if (res.old_version === res.new_version) {
        toast.success(t("fw.reflashed"), {
          description: t("fw.reflashedHint", { version: res.new_version }),
        });
      } else {
        toast.success(t("fw.updated"), { description: `${res.old_version} → ${res.new_version}.` });
      }
    } catch (e) {
      const err = e as DeviceError;
      setPhase("ready");
      stamp(t("fw.logFailed", { code: err.code ?? "error", message: err.message ?? String(e) }));
      const text = terr(err);
      toast.error(t("fw.flashFailed"), {
        description: text.hint ? `${text.message}. ${text.hint}` : text.message,
      });
    }
  }

  const pct =
    progress && progress.total_bytes > 0 && progress.phase === "flashing"
      ? Math.round((progress.done_bytes / progress.total_bytes) * 100)
      : null;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold tracking-tight">{t("fw.title")}</h1>
          <p className="text-sm text-muted-foreground">
            {t("fw.sub")}
          </p>
        </div>
        <Button variant="outline" disabled={!device.online} onClick={() => device.refresh()}>
          <RefreshCw size={15} /> {t("common.refresh")}
        </Button>
      </div>

      <div className="grid gap-5 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>
              <HardDriveDownload size={16} className="text-primary" /> {t("fw.installed")}
            </CardTitle>
            <CardDescription>{t("fw.installedSub")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-0.5">
            <InfoRow icon={HardDriveDownload} label={t("fw.version")}>
              {d.version ? <span className="font-mono">v{d.version}</span> : <NA />}
            </InfoRow>
            <InfoRow icon={HardDriveDownload} label={t("fw.mcu")}>
              {boardMcu ? <Badge variant="outline">{boardMcu}</Badge> : <NA />}
            </InfoRow>
            <InfoRow icon={HardDriveDownload} label={t("fw.product")}>
              {boardProduct ?? <NA />}
            </InfoRow>
            <InfoRow icon={HardDriveDownload} label={t("fw.serial")}>
              {d.serial ? <span className="font-mono text-xs">{d.serial}</span> : <NA />}
            </InfoRow>
            <InfoRow icon={HardDriveDownload} label={t("fw.secure")}>
              {!device.live.secure ? (
                <NA />
              ) : device.live.secure.secure_boot ? (
                <span className="flex flex-wrap items-center gap-1">
                  <Badge variant="warning">{t("dash.on")}</Badge>
                  {device.live.secure.locked && <Badge variant="destructive">{t("dash.locked")}</Badge>}
                </span>
              ) : (
                <Badge variant="outline">{t("dash.off")}</Badge>
              )}
            </InfoRow>
            {device.live.secure?.secure_boot && device.live.secure.locked && (
              <p className="pt-2 text-sm text-amber-500">
                {t("fw.secureLocked")}
              </p>
            )}
            {boardProduct !== null && boardProduct !== "HSM" && (
              <p className="pt-2 text-sm text-amber-500">
                {t("fw.nonHsm", { product: boardProduct })}
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>
              <FileUp size={16} className="text-primary" /> {t("fw.fileTitle")}
            </CardTitle>
            <CardDescription>{t("fw.fileSub")}</CardDescription>
          </CardHeader>
          <CardContent>
            <Button variant="outline" disabled={!device.online || picking || phase === "flashing"} onClick={() => void pickFile()}>
              <FileUp size={15} /> {picking ? t("fw.pickOpening") : file ? t("fw.pickAnother") : t("fw.pickChoose")}
            </Button>
            {file ? (
              <div className="mt-4 divide-y divide-border/50">
                <CheckRow ok={fileOk} label={t("fw.validImage")} detail={t("fw.blocksDetail", { blocks: file.blocks, size: formatBytes(file.bytes) })} />
                <CheckRow ok={file.mcu !== "unknown"} label={t("fw.targetMcu")} detail={t("fw.mcuDetail", { mcu: file.mcu, family: file.family_hex })} />
                <CheckRow
                  ok={file.core !== "unknown"}
                  label={t("fw.payloadCore")}
                  detail={t("fw.coreDetail", { mcu: file.mcu, core: file.core })}
                />
                <CheckRow
                  ok={mcuOk}
                  label={t("fw.matchBoard")}
                  detail={boardMcu ? t("fw.matchDetail", { file: file.mcu, board: boardMcu }) : t("fw.noBoard")}
                />
                <CheckRow ok={productOk} label={t("fw.isHsm")} detail={boardProduct ?? t("fw.noBoard")} />
                {file && boardMcu === "RP2350" && file.core !== "ARM" && (
                  <p className="pt-1 text-xs text-amber-500">
                    {t("fw.coreWarn", { core: file.core })}
                  </p>
                )}
                <p className="truncate pt-2 font-mono text-xs text-muted-foreground" title={file.path}>
                  {file.path}
                </p>
                <div className="mt-3 space-y-2 border-t border-border/50 pt-3">
                  <label className="flex cursor-pointer items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={verifyOn}
                      onChange={(e) => {
                        setVerifyOn(e.target.checked);
                        setHashOk(null);
                      }}
                    />
                    {t("fw.verifyHash")}
                  </label>
                  {verifyOn && (
                    <div className="space-y-2">
                      {file.sidecar_hash ? (
                        <p className="font-mono text-xs text-muted-foreground" title={file.sidecar_hash}>
                          Sidecar found: {file.sidecar_hash.slice(0, 16)}…{file.sidecar_hash.slice(-8)}
                          {hashOk === true && <span className="text-green-500"> · {t("fw.sidecarOk")}</span>}
                          {hashOk === false && <span className="text-red-500"> · {t("fw.sidecarBad")}</span>}
                        </p>
                      ) : (
                        <p className="text-xs text-muted-foreground">
                          {t("fw.noSidecar")}
                        </p>
                      )}
                      <div className="flex gap-2">
                        <input
                          type="text"
                          value={pasted}
                          spellCheck={false}
                          onChange={(e) => {
                            setPasted(e.target.value);
                            setHashOk(null);
                          }}
                          placeholder={file.sidecar_hash ?? t("fw.pastePh")}
                          className="min-w-0 flex-1 rounded border border-border bg-background px-2 py-1.5 font-mono text-xs"
                        />
                        <Button
                          variant="outline"
                          disabled={hashBusy || pasted.trim() === ""}
                          onClick={() => void checkHash(file.path, pasted.trim())}
                        >
                          {hashBusy ? t("fw.verifying") : t("fw.verifyBtn")}
                        </Button>
                      </div>
                      {hashOk === true && <p className="text-xs text-green-500">{t("fw.pasteOk")}</p>}
                      {hashOk === false && <p className="text-xs text-red-500">{t("fw.pasteBad")}</p>}
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <p className="mt-4 text-sm text-muted-foreground">{t("fw.noFile")}</p>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>
            <HardDriveDownload size={16} className="text-primary" /> {t("fw.flashTitle")}
          </CardTitle>
          <CardDescription>{t("fw.flashSub")}</CardDescription>
        </CardHeader>
        <CardContent>
          {phase === "flashing" && progress ? (
            <div className="space-y-2">
              <p className="text-sm font-medium">{phaseLabel(t, progress.phase)}</p>
              {progress.phase === "power-cycle" ? (
                <p className="text-sm text-amber-500">
                  {t("fw.powerCycleHint")}
                </p>
              ) : pct !== null ? (
                <>
                  <div className="h-2 overflow-hidden rounded-full bg-muted">
                    <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${pct}%` }} />
                  </div>
                  <p className="font-mono text-xs text-muted-foreground">
                    {formatBytes(progress.done_bytes)} / {formatBytes(progress.total_bytes)} ({pct}%)
                  </p>
                </>
              ) : (
                <p className="text-sm text-muted-foreground">{t("fw.doNotUnplug")}</p>
              )}
            </div>
          ) : phase === "done" && result ? (
            <div className="flex flex-wrap items-center gap-3">
              <Badge variant="success">
                v{result.old} → v{result.current}
              </Badge>
              <span className="text-sm text-muted-foreground">
                {result.old === result.current
                  ? t("fw.reflashedNote")
                  : t("fw.flashDone")}
              </span>
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-3">
              <Button variant="primary" disabled={!canFlash} onClick={() => setConfirming(true)}>
                <HardDriveDownload size={15} /> {t("fw.flashBtn")}
              </Button>
              {!device.online && <span className="text-sm text-muted-foreground">{t("fw.connectFirst")}</span>}
              {file && (!mcuOk || !productOk || !hashPass) && device.online && (
                <span className="text-sm text-amber-500">
                  {!hashPass ? t("fw.hashBlocked") : t("fw.mismatchBlocked")}
                </span>
              )}
            </div>
          )}
          {log.length > 0 && (
            <div className="mt-4 rounded border border-border/50 bg-muted/30 p-3">
              <p className="mb-1 text-xs font-medium text-muted-foreground">{t("fw.logTitle")}</p>
              <pre className="max-h-32 overflow-y-auto font-mono text-xs leading-relaxed">{log.join("\n")}</pre>
            </div>
          )}
          <details className="mt-4 text-sm text-muted-foreground">
            <summary className="cursor-pointer hover:text-foreground">{t("fw.manualTitle")}</summary>
            <p className="mt-1">
              {t("fw.manualBody")}
            </p>
          </details>
        </CardContent>
      </Card>

      {confirming && file && (
        <ConfirmDialog
          title={t("fw.confirmTitle", { mcu: boardMcu ?? "?" })}
          description={t("fw.confirmDesc", {
            size: formatBytes(file.bytes),
            blocks: file.blocks,
            family: file.family_hex,
            hash: verifyOn ? t("fw.confirmDescHash") : "",
          })}
          confirmLabel={t("fw.confirmNow")}
          danger
          busy={phase === "flashing" || hashBusy}
          onConfirm={() => void runFlash()}
          onCancel={() => setConfirming(false)}
        />
      )}
    </div>
  );
}
