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
import type { DeviceState } from "../hooks/useDevice";

type Phase = "idle" | "ready" | "flashing" | "done";

const PHASE_LABEL: Record<string, string> = {
  "waiting-drive": "Waiting for BOOTSEL drive…",
  flashing: "Writing firmware…",
  "waiting-reboot": "Waiting for the board to reboot…",
  "power-cycle": "Image transferred — board needs a power cycle",
  done: "Done",
};

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

export function Firmware({ device }: { device: DeviceState }) {
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
      const err = e as DeviceError;
      setHashOk(false);
      toast.error("Hash mismatch", {
        description: err.hint ? `${err.message}. ${err.hint}` : err.message,
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
          toast.warning("Unknown UF2 family", { description: "This file does not target RP2040/RP2350." });
        }
        // Hash check defaults to the sidecar when present, stays optional.
        const auto = info.sidecar_hash !== null;
        setVerifyOn(auto);
        setPasted("");
        setHashOk(null);
        if (auto && info.sidecar_hash) {
          const ok = await checkHash(info.path, info.sidecar_hash);
          if (ok) toast.success("Hash verified", { description: "Sidecar SHA-256 matches the file." });
        }
      } catch (e) {
        const err = e as DeviceError;
        setFile(null);
        setPhase("idle");
        toast.error("Invalid firmware file", {
          description: err.hint ? `${err.message}. ${err.hint}` : err.message,
        });
      }
    } finally {
      setPicking(false);
    }
  }

  function stamp(msg: string) {
    const t = new Date().toLocaleTimeString("de-DE", { hour12: false });
    setLog((l) => [...l.slice(-49), `${t} ${msg}`]);
  }

  function onFlashProgress(p: FlashProgress) {
    setProgress(p);
    if (p.phase !== lastPhase.current) {
      lastPhase.current = p.phase;
      stamp(
        p.phase === "flashing"
          ? "Copying to BOOTSEL drive…"
          : (PHASE_LABEL[p.phase] ?? p.phase),
      );
    }
  }

  async function runFlash() {
    if (!file) return;
    setConfirming(false);
    setLog([]);
    lastPhase.current = "";
    stamp(`Start: ${file.blocks} blocks, ${formatBytes(file.bytes)} → BOOTSEL reboot (press the device button if asked)`);
    // Optional hash gate runs BEFORE any reboot — the board stays untouched.
    if (verifyOn) {
      const expected = pasted.trim() !== "" ? pasted.trim() : (file.sidecar_hash ?? "");
      if (expected === "") {
        toast.error("No expected hash", { description: "Paste a SHA-256 or disable verification." });
        return;
      }
      const ok = await checkHash(file.path, expected);
      if (!ok) return;
    }
    setPhase("flashing");
    setProgress({ phase: "waiting-drive", done_bytes: 0, total_bytes: file.bytes });
    try {
      const res = await device.flashFirmware(file.path, onFlashProgress);
      stamp(`Board back, version v${res.new_version}`);
      setResult({ old: res.old_version, current: res.new_version });
      setPhase("done");
      if (res.old_version === res.new_version) {
        toast.success("Firmware re-flashed", {
          description: `Version unchanged (v${res.new_version}) — the file carries no version proof.`,
        });
      } else {
        toast.success("Firmware updated", { description: `${res.old_version} → ${res.new_version}.` });
      }
    } catch (e) {
      const err = e as DeviceError;
      setPhase("ready");
      stamp(`FAILED: ${err.code ?? "error"} — ${err.message ?? String(e)}`);
      toast.error("Flash failed", {
        description: err.hint ? `${err.message}. ${err.hint}` : err.message || String(e),
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
          <h1 className="text-xl font-bold tracking-tight">Firmware</h1>
          <p className="text-sm text-muted-foreground">
            Update via BOOTSEL mass storage. Keys are preserved; a DKEK backup is still recommended.
          </p>
        </div>
        <Button variant="outline" disabled={!device.online} onClick={() => device.refresh()}>
          <RefreshCw size={15} /> Refresh
        </Button>
      </div>

      <div className="grid gap-5 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>
              <HardDriveDownload size={16} className="text-primary" /> Installed
            </CardTitle>
            <CardDescription>Read from the board.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-0.5">
            <InfoRow icon={HardDriveDownload} label="Version">
              {d.version ? <span className="font-mono">v{d.version}</span> : <NA />}
            </InfoRow>
            <InfoRow icon={HardDriveDownload} label="MCU">
              {boardMcu ? <Badge variant="outline">{boardMcu}</Badge> : <NA />}
            </InfoRow>
            <InfoRow icon={HardDriveDownload} label="Product">
              {boardProduct ?? <NA />}
            </InfoRow>
            <InfoRow icon={HardDriveDownload} label="Serial">
              {d.serial ? <span className="font-mono text-xs">{d.serial}</span> : <NA />}
            </InfoRow>
            <InfoRow icon={HardDriveDownload} label="Secure boot">
              {!device.live.secure ? (
                <NA />
              ) : device.live.secure.secure_boot ? (
                <span className="flex flex-wrap items-center gap-1">
                  <Badge variant="warning">ON</Badge>
                  {device.live.secure.locked && <Badge variant="destructive">Locked</Badge>}
                </span>
              ) : (
                <Badge variant="outline">OFF</Badge>
              )}
            </InfoRow>
            {device.live.secure?.secure_boot && device.live.secure.locked && (
              <p className="pt-2 text-sm text-amber-500">
                Secure boot is locked: only images signed with the enrolled boot key will boot. An
                unsigned or foreign-signed UF2 is ignored — the board stays in BOOTSEL.
              </p>
            )}
            {boardProduct !== null && boardProduct !== "HSM" && (
              <p className="pt-2 text-sm text-amber-500">
                This flasher only serves Pico HSM boards — flashing is disabled for {boardProduct}.
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>
              <FileUp size={16} className="text-primary" /> Update file
            </CardTitle>
            <CardDescription>Local .uf2 image, validated before anything is written.</CardDescription>
          </CardHeader>
          <CardContent>
            <Button variant="outline" disabled={!device.online || picking || phase === "flashing"} onClick={() => void pickFile()}>
              <FileUp size={15} /> {picking ? "Opening…" : file ? "Choose another file" : "Choose .uf2 file"}
            </Button>
            {file ? (
              <div className="mt-4 divide-y divide-border/50">
                <CheckRow ok={fileOk} label="Valid UF2 image" detail={`${file.blocks} blocks · ${formatBytes(file.bytes)}`} />
                <CheckRow ok={file.mcu !== "unknown"} label="Targets Pico MCU" detail={`${file.mcu} (${file.family_hex})`} />
                <CheckRow
                  ok={file.core !== "unknown"}
                  label="Payload core"
                  detail={`${file.mcu} ${file.core}`}
                />
                <CheckRow
                  ok={mcuOk}
                  label="Matches connected board"
                  detail={boardMcu ? `file ${file.mcu} · board ${boardMcu}` : "no board"}
                />
                <CheckRow ok={productOk} label="Board is Pico HSM" detail={boardProduct ?? "no board"} />
                {file && boardMcu === "RP2350" && file.core !== "ARM" && (
                  <p className="pt-1 text-xs text-amber-500">
                    This image targets {file.core}; most RP2350 boards boot ARM. The rescue applet cannot
                    report the board core — a core mismatch is silently ignored (board stays in BOOTSEL).
                    Verify against your firmware build before flashing.
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
                    Verify SHA-256 before flashing (optional)
                  </label>
                  {verifyOn && (
                    <div className="space-y-2">
                      {file.sidecar_hash ? (
                        <p className="font-mono text-xs text-muted-foreground" title={file.sidecar_hash}>
                          Sidecar found: {file.sidecar_hash.slice(0, 16)}…{file.sidecar_hash.slice(-8)}
                          {hashOk === true && <span className="text-green-500"> · verified</span>}
                          {hashOk === false && <span className="text-red-500"> · mismatch</span>}
                        </p>
                      ) : (
                        <p className="text-xs text-muted-foreground">
                          No <span className="font-mono">.sha256</span> sidecar next to the file — paste the
                          expected hash from the release notes.
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
                          placeholder={file.sidecar_hash ?? "SHA-256 hex (64 chars, overrides sidecar)"}
                          className="min-w-0 flex-1 rounded border border-border bg-background px-2 py-1.5 font-mono text-xs"
                        />
                        <Button
                          variant="outline"
                          disabled={hashBusy || pasted.trim() === ""}
                          onClick={() => void checkHash(file.path, pasted.trim())}
                        >
                          {hashBusy ? "Checking…" : "Verify"}
                        </Button>
                      </div>
                      {hashOk === true && <p className="text-xs text-green-500">Pasted hash matches the file.</p>}
                      {hashOk === false && <p className="text-xs text-red-500">Hash does not match — flashing is blocked.</p>}
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <p className="mt-4 text-sm text-muted-foreground">No file selected.</p>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>
            <HardDriveDownload size={16} className="text-primary" /> Flash
          </CardTitle>
          <CardDescription>Reboots to BOOTSEL, copies the image, waits for the board to return.</CardDescription>
        </CardHeader>
        <CardContent>
          {phase === "flashing" && progress ? (
            <div className="space-y-2">
              <p className="text-sm font-medium">{PHASE_LABEL[progress.phase] ?? progress.phase}</p>
              {progress.phase === "power-cycle" ? (
                <p className="text-sm text-amber-500">
                  The image was consumed but the board stays in BOOTSEL. Unplug the board (or press
                  RESET) and plug it back in — the app finishes automatically once it is back.
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
                <p className="text-sm text-muted-foreground">Do not unplug the board.</p>
              )}
            </div>
          ) : phase === "done" && result ? (
            <div className="flex flex-wrap items-center gap-3">
              <Badge variant="success">
                v{result.old} → v{result.current}
              </Badge>
              <span className="text-sm text-muted-foreground">
                {result.old === result.current
                  ? "Re-flashed with the same version (UF2 carries no version proof)."
                  : "Flash complete. The board is back online."}
              </span>
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-3">
              <Button variant="primary" disabled={!canFlash} onClick={() => setConfirming(true)}>
                <HardDriveDownload size={15} /> Flash firmware
              </Button>
              {!device.online && <span className="text-sm text-muted-foreground">Connect a board first.</span>}
              {file && (!mcuOk || !productOk || !hashPass) && device.online && (
                <span className="text-sm text-amber-500">
                  {!hashPass ? "SHA-256 not verified — flashing disabled." : "File and board do not match — flashing disabled."}
                </span>
              )}
            </div>
          )}
          {log.length > 0 && (
            <div className="mt-4 rounded border border-border/50 bg-muted/30 p-3">
              <p className="mb-1 text-xs font-medium text-muted-foreground">Flash log</p>
              <pre className="max-h-32 overflow-y-auto font-mono text-xs leading-relaxed">{log.join("\n")}</pre>
            </div>
          )}
          <details className="mt-4 text-sm text-muted-foreground">
            <summary className="cursor-pointer hover:text-foreground">Manual fallback</summary>
            <p className="mt-1">
              Reboot to BOOTSEL (Dashboard → Reboot), then drag the .uf2 file onto the RPI-RP2 drive in
              Explorer. The board reboots itself when the copy finishes.
            </p>
          </details>
        </CardContent>
      </Card>

      {confirming && file && (
        <ConfirmDialog
          title={`Flash firmware onto ${boardMcu}?`}
          description={`Step 1: press the device button and release it when the LED asks. Step 2: writes ${formatBytes(file.bytes)} (${file.blocks} blocks, family ${file.family_hex}) via BOOTSEL. The board reboots twice. If the board stays in BOOTSEL after the copy, a quick unplug/replug finishes the reboot. Keys are preserved, but a DKEK backup is recommended before any firmware update.${verifyOn ? " SHA-256 is re-verified right before flashing." : ""}`}
          confirmLabel="Flash now"
          danger
          busy={false}
          onConfirm={() => void runFlash()}
          onCancel={() => setConfirming(false)}
        />
      )}
    </div>
  );
}
