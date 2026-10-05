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
import { useLang } from "../lib/i18n/LangContext";
import type { DeviceState } from "../hooks/useDevice";
import type { PinStatus } from "../lib/tauri";

function PinCounter({ pin }: { pin: PinStatus | null }) {
  const { t, tp } = useLang();
  if (!pin) return <NA />;
  if (pin.blocked) return <Badge variant="destructive">{t("dash.pinBlocked")}</Badge>;
  if (pin.retries < 0) return <Badge variant="info">{t("dash.noPinRequired")}</Badge>;
  // No "/ max": the configured maximum varies (init allows 1-15), only the
  // remaining count is device truth. "Low" at 2 or fewer.
  return (
    <>
      <span className="font-mono">{tp("dash.triesLeft", pin.retries)}</span>
      {pin.retries <= 2 && <Badge variant="warning">{t("dash.triesLow")}</Badge>}
    </>
  );
}

function OptBadge({ on }: { on: boolean | undefined }) {
  const { t } = useLang();
  if (on === undefined) return <NA />;
  return <Badge variant={on ? "success" : "outline"}>{on ? t("dash.on") : t("dash.off")}</Badge>;
}

export function Dashboard({ device, onSetup }: { device: DeviceState; onSetup: () => void }) {
  const { t, tp } = useLang();
  const d = device.device;
  const m = device.memory;
  const usedPct = m ? Math.round((m.usedBytes / m.totalBytes) * 100) : 0;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold tracking-tight">{t("dash.title")}</h1>
          <p className="text-sm text-muted-foreground">
            {device.live.present
              ? t("dash.subtitleOnline", { reader: device.live.reader ?? "reader" })
              : t("dash.subtitleOffline")}
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="primary" onClick={() => device.refresh()}>
            <RefreshCw size={15} /> {t("common.refresh")}
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
              <p className="text-sm font-semibold">{t("dash.noBoard")}</p>
              <p className="text-sm text-muted-foreground">
                {device.transportNote ?? t("dash.noBoardHint")}
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
              <p className="text-sm font-semibold">{t("dash.notInitialized")}</p>
              <p className="text-sm text-muted-foreground">
                {t("dash.notInitializedHint")}
              </p>
            </div>
            <Button variant="primary" onClick={onSetup}>
              {t("dash.setupNow")}
            </Button>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-5 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>
              <Boxes size={16} className="text-primary" /> {t("dash.deviceInfo")}
            </CardTitle>
            <CardDescription>{t("dash.deviceInfoSub")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-0.5">
            <InfoRow icon={Cable} label={t("dash.connection")}>
              {d.connection ? (
                <Badge variant={d.connection === "RESCUE" ? "warning" : "success"}>{d.connection}</Badge>
              ) : (
                <NA />
              )}
            </InfoRow>
            <InfoRow icon={Package} label={t("dash.product")}>
              {d.product ?? <NA />}
            </InfoRow>
            <InfoRow icon={Cpu} label={t("dash.platform")}>
              {d.platform ? <Badge variant="outline">{d.platform}</Badge> : <NA />}
            </InfoRow>
            <InfoRow icon={Tag} label={t("dash.version")}>
              {d.version ? <span className="font-mono">v{d.version}</span> : <NA />}
            </InfoRow>
            <InfoRow icon={Fingerprint} label={t("dash.serialNumber")}>
              {d.serial ? (
                <>
                  <span className="font-mono text-xs">{d.serial}</span>
                  <button
                    className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                    title={t("dash.copySerial")}
                    onClick={async () => {
                      try {
                        await navigator.clipboard.writeText(d.serial ?? "");
                        toast.success(t("dash.serialCopied"), { description: d.serial ?? "" });
                      } catch {
                        toast.info(t("dash.serialTitle"), { description: d.serial ?? "" });
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
            <InfoRow icon={Nfc} label={t("dash.reader")}>
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
              <MemoryStick size={16} className="text-accent" /> {t("dash.memoryInfo")}
            </CardTitle>
            <CardDescription>{t("dash.memoryInfoSub")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-0.5">
            <InfoRow icon={PieChart} label={t("dash.freeMemory")}>
              {m ? <span className="font-mono text-emerald-500">{formatBytes(m.freeBytes)}</span> : <NA />}
            </InfoRow>
            <InfoRow icon={HardDrive} label={t("dash.usedMemory")}>
              {m ? <span className="font-mono">{formatBytes(m.usedBytes)}</span> : <NA />}
            </InfoRow>
            <InfoRow icon={Database} label={t("dash.totalMemory")}>
              {m ? <span className="font-mono">{formatBytes(m.totalBytes)}</span> : <NA />}
            </InfoRow>
            {m ? (
              <div className="px-3 py-2">
                <div className="mb-1 flex justify-between text-xs text-muted-foreground">
                  <span>{t("dash.usage")}</span>
                  <span className="font-mono">{usedPct}%</span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-muted">
                  <div className="h-full rounded-full bg-gradient-to-r from-primary to-accent" style={{ width: `${usedPct}%` }} />
                </div>
              </div>
            ) : null}
            <InfoRow icon={Files} label={t("dash.fileCount")}>
              {m ? <Badge variant="outline">{tp("dash.files", m.fileCount)}</Badge> : <NA />}
            </InfoRow>
            <InfoRow icon={FileArchive} label={t("dash.filesystemSize")}>
              {m ? <span className="font-mono">{formatBytes(m.filesystemBytes)}</span> : <NA />}
            </InfoRow>
            <InfoRow icon={Microchip} label={t("dash.firmwareSize")}>
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
            <ShieldCheck size={16} className="text-emerald-500" /> {t("dash.securityTitle")}
          </CardTitle>
          <CardDescription>{t("dash.securitySub")}</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-x-6 sm:grid-cols-2">
          <InfoRow icon={ShieldCheck} label={t("dash.initState")}>
            {device.initState === "initialized" ? <Badge variant="success">{t("dash.initialized")}</Badge> : <NA />}
          </InfoRow>
          <InfoRow icon={KeyRound} label={t("dash.session")}>
            {!device.online ? (
              <NA />
            ) : device.unlocked ? (
              <>
                <Badge variant="success">{t("dash.unlocked")}</Badge>
                <Button
                  variant="ghost"
                  className="h-7 px-2 text-xs"
                  onClick={() => {
                    device.logout();
                    toast.info(t("dash.lock"), { description: t("dash.lockedHint") });
                  }}
                >
                  {t("dash.lock")}
                </Button>
              </>
            ) : (
              <>
                <Badge variant="outline">{t("dash.locked")}</Badge>
                <Button variant="primary" className="h-7 px-3 text-xs" onClick={() => device.requestPin()}>
                  <KeyRound size={13} /> {t("dash.unlock")}
                </Button>
              </>
            )}
          </InfoRow>
          <InfoRow icon={KeyRound} label={t("dash.pinRetries")}>
            <PinCounter pin={device.live.pin} />
          </InfoRow>
          <InfoRow icon={KeyRound} label={t("dash.sopinRetries")}>
            <PinCounter pin={device.live.sopin} />
          </InfoRow>
          <InfoRow icon={MousePointerClick} label={t("dash.pressConfirm")}>
            <OptBadge on={device.securityOpts?.pressConfirm} />
          </InfoRow>
          <InfoRow icon={Hash} label={t("dash.keyCounter")}>
            <OptBadge on={device.securityOpts?.keyCounter} />
          </InfoRow>
          <InfoRow icon={Lock} label={t("dash.secureLock")}>
            <OptBadge on={device.securityOpts?.secureLock} />
          </InfoRow>
          <InfoRow icon={BadgeCheck} label={t("dash.secureBoot")}>
            {!device.live.secure ? (
              <NA />
            ) : device.live.secure.secure_boot ? (
              <>
                <Badge variant="success">{t("dash.on")}</Badge>
                {device.live.secure.locked && <Badge variant="warning">{t("dash.locked")}</Badge>}
                <span className="font-mono text-xs text-muted-foreground" title={t("dash.bootKeyIndex")}>
                  key {device.live.secure.boot_key}
                </span>
              </>
            ) : (
              <Badge variant="outline">{t("dash.off")}</Badge>
            )}
          </InfoRow>
        </CardContent>
      </Card>
    </div>
  );
}
