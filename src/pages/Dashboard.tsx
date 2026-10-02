import {
  Boxes,
  Cable,
  Copy,
  Cpu,
  Database,
  FileArchive,
  Files,
  Fingerprint,
  HardDrive,
  Hash,
  KeyRound,
  BadgeCheck,
  Lock,
  MemoryStick,
  Microchip,
  MousePointerClick,
  Nfc,
  Package,
  PieChart,
  RefreshCw,
  ShieldCheck,
  Tag,
  Usb,
} from "lucide-react";
import { toast } from "sonner";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/Card";
import { NA } from "../components/ui/NA";
import { InfoRow } from "../components/InfoRow";
import { formatBytes } from "../lib/format";
import type { DeviceState } from "../hooks/useDevice";
import type { PinStatus } from "../lib/tauri";

function PinCounter({ pin, max }: { pin: PinStatus | null; max: number }) {
  if (!pin) return <NA />;
  if (pin.blocked) return <Badge variant="destructive">Blocked</Badge>;
  if (pin.retries < 0) return <Badge variant="info">No PIN required</Badge>;
  return (
    <>
      <span className="font-mono">
        {pin.retries} / {max} left
      </span>
      {pin.retries < max && <Badge variant="warning">Low</Badge>}
    </>
  );
}

function OptBadge({ on }: { on: boolean | undefined }) {
  if (on === undefined) return <NA />;
  return <Badge variant={on ? "success" : "outline"}>{on ? "ON" : "OFF"}</Badge>;
}

export function Dashboard({ device, onSetup }: { device: DeviceState; onSetup: () => void }) {
  const d = device.device;
  const m = device.memory;
  const usedPct = m ? Math.round((m.usedBytes / m.totalBytes) * 100) : 0;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold tracking-tight">Dashboard</h1>
          <p className="text-sm text-muted-foreground">
            {device.live.present
              ? `Connected via ${device.live.reader ?? "reader"} · all values read from the device.`
              : "Device overview, memory and security status."}
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="primary" onClick={() => device.refresh()}>
            <RefreshCw size={15} /> Refresh
          </Button>
        </div>
      </div>

      {!device.online && (
        <Card className="border-amber-500/30 bg-amber-500/5">
          <CardContent className="flex items-center gap-4 p-5">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-amber-500/15 text-amber-500">
              <Usb size={20} />
            </span>
            <div>
              <p className="text-sm font-semibold">No board connected</p>
              <p className="text-sm text-muted-foreground">
                {device.transportNote ?? "Plug in your Pico HSM to see live values."}
              </p>
            </div>
          </CardContent>
        </Card>
      )}

      {device.online && device.initState === "uninitialized" && (
        <Card className="border-amber-500/30 bg-amber-500/5">
          <CardContent className="flex flex-wrap items-center gap-4 p-5">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-amber-500/15 text-amber-500">
              <KeyRound size={20} />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold">Device not initialized</p>
              <p className="text-sm text-muted-foreground">
                No PIN set yet — set User-PIN and SO-PIN first. Initialization erases all keys.
              </p>
            </div>
            <Button variant="primary" onClick={onSetup}>
              Set up now
            </Button>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-5 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>
              <Boxes size={16} className="text-primary" /> Device Information
            </CardTitle>
            <CardDescription>Firmware-reported identity.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-0.5">
            <InfoRow icon={Cable} label="Connection">
              {d.connection ? (
                <Badge variant={d.connection === "RESCUE" ? "warning" : "success"}>{d.connection}</Badge>
              ) : (
                <NA />
              )}
            </InfoRow>
            <InfoRow icon={Package} label="Product">
              {d.product ?? <NA />}
            </InfoRow>
            <InfoRow icon={Cpu} label="Platform">
              {d.platform ? <Badge variant="outline">{d.platform}</Badge> : <NA />}
            </InfoRow>
            <InfoRow icon={Tag} label="Version">
              {d.version ? <span className="font-mono">v{d.version}</span> : <NA />}
            </InfoRow>
            <InfoRow icon={Fingerprint} label="Serial number">
              {d.serial ? (
                <>
                  <span className="font-mono text-xs">{d.serial}</span>
                  <button
                    className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                    title="Copy serial"
                    onClick={async () => {
                      try {
                        await navigator.clipboard.writeText(d.serial ?? "");
                        toast.success("Serial copied", { description: d.serial ?? "" });
                      } catch {
                        toast.info("Serial", { description: d.serial ?? "" });
                      }
                    }}
                  >
                    <Copy size={13} />
                  </button>
                </>
              ) : (
                <NA />
              )}
            </InfoRow>
            <InfoRow icon={Nfc} label="Reader">
              {device.live.reader ? (
                <>
                  <span className="max-w-[260px] truncate" title={device.live.reader}>
                    {device.live.reader}
                  </span>
                  {device.live.protocol ? <Badge variant="outline">{device.live.protocol}</Badge> : <NA />}
                </>
              ) : (
                <NA />
              )}
            </InfoRow>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>
              <MemoryStick size={16} className="text-accent" /> Memory Information
            </CardTitle>
            <CardDescription>Flash usage from the rescue applet (FLASH INFO).</CardDescription>
          </CardHeader>
          <CardContent className="space-y-0.5">
            <InfoRow icon={PieChart} label="Free memory">
              {m ? <span className="font-mono text-emerald-500">{formatBytes(m.freeBytes)}</span> : <NA />}
            </InfoRow>
            <InfoRow icon={HardDrive} label="Used memory">
              {m ? <span className="font-mono">{formatBytes(m.usedBytes)}</span> : <NA />}
            </InfoRow>
            <InfoRow icon={Database} label="Total memory">
              {m ? <span className="font-mono">{formatBytes(m.totalBytes)}</span> : <NA />}
            </InfoRow>
            {m ? (
              <div className="px-3 py-2">
                <div className="mb-1 flex justify-between text-xs text-muted-foreground">
                  <span>Usage</span>
                  <span className="font-mono">{usedPct}%</span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-muted">
                  <div className="h-full rounded-full bg-gradient-to-r from-primary to-accent" style={{ width: `${usedPct}%` }} />
                </div>
              </div>
            ) : null}
            <InfoRow icon={Files} label="Number of files">
              {m ? <Badge variant="outline">{m.fileCount} files</Badge> : <NA />}
            </InfoRow>
            <InfoRow icon={FileArchive} label="Total filesystem size">
              {m ? <span className="font-mono">{formatBytes(m.filesystemBytes)}</span> : <NA />}
            </InfoRow>
            <InfoRow icon={Microchip} label="Firmware size">
              {m?.firmwareBytes != null ? (
                <span className="font-mono">{formatBytes(m.firmwareBytes)}</span>
              ) : (
                <NA />
              )}
            </InfoRow>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>
            <ShieldCheck size={16} className="text-emerald-500" /> Security status
          </CardTitle>
          <CardDescription>PIN counters and device options — readable without login.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-x-6 sm:grid-cols-2">
          <InfoRow icon={ShieldCheck} label="Init state">
            {device.initState === "initialized" ? <Badge variant="success">Initialized</Badge> : <NA />}
          </InfoRow>
          <InfoRow icon={KeyRound} label="Session">
            {!device.online ? (
              <NA />
            ) : device.unlocked ? (
              <>
                <Badge variant="success">Unlocked</Badge>
                <button
                  className="text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
                  onClick={() => {
                    device.logout();
                    toast.info("Locked", { description: "PIN cleared from memory. The device itself stays unlocked until unplugged." });
                  }}
                >
                  Lock
                </button>
              </>
            ) : (
              <>
                <Badge variant="outline">Locked</Badge>
                <button
                  className="text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
                  onClick={() => device.requestPin()}
                >
                  Unlock
                </button>
              </>
            )}
          </InfoRow>
          <InfoRow icon={KeyRound} label="UserPIN retries">
            <PinCounter pin={device.live.pin} max={device.pinMax.user} />
          </InfoRow>
          <InfoRow icon={KeyRound} label="SOPIN retries">
            <PinCounter pin={device.live.sopin} max={device.pinMax.so} />
          </InfoRow>
          <InfoRow icon={MousePointerClick} label="Press-to-confirm">
            <OptBadge on={device.securityOpts?.pressConfirm} />
          </InfoRow>
          <InfoRow icon={Hash} label="Key counter">
            <OptBadge on={device.securityOpts?.keyCounter} />
          </InfoRow>
          <InfoRow icon={Lock} label="Secure lock">
            <OptBadge on={device.securityOpts?.secureLock} />
          </InfoRow>
          <InfoRow icon={BadgeCheck} label="Secure boot">
            {!device.live.secure ? (
              <NA />
            ) : device.live.secure.secure_boot ? (
              <>
                <Badge variant="success">ON</Badge>
                {device.live.secure.locked && <Badge variant="warning">Locked</Badge>}
                <span className="font-mono text-xs text-muted-foreground" title="Boot key index">
                  key {device.live.secure.boot_key}
                </span>
              </>
            ) : (
              <Badge variant="outline">OFF</Badge>
            )}
          </InfoRow>
        </CardContent>
      </Card>
    </div>
  );
}
