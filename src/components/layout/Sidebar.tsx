import { useEffect, useRef, useState } from "react";
import {
  Archive,
  Award,
  CircuitBoard,
  Cpu,
  HardDriveDownload,
  KeyRound,
  LayoutDashboard,
  Moon,
  ScrollText,
  ShieldCheck,
  Sun,
  Wifi,
  WifiOff,
  Clock3,
  Usb,
} from "lucide-react";
import { toast } from "sonner";
import { cn } from "../../lib/utils";
import { formatClock } from "../../lib/format";
import { useLang } from "../../lib/i18n/LangContext";
import type { DeviceState } from "../../hooks/useDevice";
import { Switch } from "../ui/Switch";
import { Badge } from "../ui/Badge";

export type TabId = "dashboard" | "keys" | "certs" | "backup" | "device" | "firmware" | "logs";

/** Device clock, advanced locally each second. Owns its ticker so the rest
 * of the app does NOT re-render every second (used to live in useDevice). */
function Clock({ rtcDate }: { rtcDate: Date | null }) {
  const [, setTick] = useState(0);
  const liveAt = useRef(Date.now());
  useEffect(() => {
    liveAt.current = Date.now();
  }, [rtcDate]);
  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, []);
  const now = !rtcDate ? null : new Date(rtcDate.getTime() + (Date.now() - liveAt.current));
  return (
    <span className="font-mono text-xs font-medium tabular-nums">{now ? formatClock(now) : "--"}</span>
  );
}

export const tabs: { id: TabId; icon: typeof LayoutDashboard }[] = [
  { id: "dashboard", icon: LayoutDashboard },
  { id: "keys", icon: KeyRound },
  { id: "certs", icon: Award },
  { id: "backup", icon: Archive },
  { id: "device", icon: Cpu },
  { id: "firmware", icon: HardDriveDownload },
  { id: "logs", icon: ScrollText },
];

export function Sidebar({
  active,
  onNavigate,
  device,
  dark,
  onToggleTheme,
}: {
  active: TabId;
  onNavigate: (t: TabId) => void;
  device: DeviceState;
  dark: boolean;
  onToggleTheme: () => void;
}) {
  // Two-step confirm: first toggle arms, second toggle within 6 s reboots.
  const [armed, setArmed] = useState(false);
  const [rebooting, setRebooting] = useState(false);
  const { lang, setLang, t, terr } = useLang();
  useEffect(() => {
    if (!armed) return;
    const timer = setTimeout(() => setArmed(false), 6000);
    return () => clearTimeout(timer);
  }, [armed]);

  async function handleBootsel(v: boolean) {
    if (rebooting) return;
    if (!v) {
      setArmed(false);
      device.setBootsel(false);
      return;
    }
    if (!device.online) {
      toast.error(t("sidebar.noDevice"), { description: t("sidebar.noDeviceHint") });
      return;
    }
    if (!armed) {
      setArmed(true);
      toast.warning(t("sidebar.confirmBootsel"), {
        description: t("sidebar.confirmBootselHint"),
      });
      return;
    }
    setArmed(false);
    setRebooting(true);
    try {
      const msg = await device.rebootDevice(true);
      device.setBootsel(true);
      toast.warning(t("sidebar.rebootingBootsel"), { description: msg });
    } catch (e) {
      const err = e as { code?: string; message?: string; hint?: string };
      const text = terr(err as import("../../lib/tauri").DeviceError);
      toast.error(err.code === "RebootUnsupported" ? t("sidebar.rebootNotSupported") : t("sidebar.rebootFailed"), {
        description: text.hint || text.message,
      });
    } finally {
      setRebooting(false);
    }
  }

  return (
    <aside className="flex h-full w-[280px] shrink-0 flex-col border-r border-border bg-card">
      <div className="flex items-center gap-3 border-b border-border px-5 py-4">
        <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary text-primary-foreground">
          <ShieldCheck size={20} />
        </span>
        <div className="min-w-0">
          <p className="truncate text-sm font-bold leading-tight">Pico HSM App</p>
          <p className="flex items-center gap-1 text-xs text-muted-foreground">
            <CircuitBoard size={12} /> HSM Manager · v0.1.0
          </p>
        </div>
      </div>

      <nav className="flex-1 space-y-1 overflow-y-auto p-3">
        {tabs.map((tab) => {
          const Icon = tab.icon;
          const isActive = tab.id === active;
          const gated = tab.id !== "dashboard" && !device.online;
          const label = t(`nav.${tab.id}`);
          return (
            <button
              key={tab.id}
              onClick={() => onNavigate(tab.id)}
              disabled={gated}
              title={gated ? t("app.connectFirst") : label}
              className={cn(
                "flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors",
                isActive ? "bg-primary text-primary-foreground shadow-sm" : "text-muted-foreground hover:bg-muted hover:text-foreground",
                gated && "cursor-not-allowed opacity-40 hover:bg-transparent hover:text-muted-foreground",
              )}
            >
              <Icon size={17} />
              {label}
            </button>
          );
        })}
      </nav>

      <div className="space-y-3 border-t border-border p-4">
        <div className="flex items-center justify-between">
          <span className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
            {device.online ? <Wifi size={14} className="text-emerald-500" /> : <WifiOff size={14} className="text-muted-foreground" />}
            {t("sidebar.device")}
          </span>
          <Badge variant={device.online ? "success" : "outline"}>
            <span className={cn("h-1.5 w-1.5 rounded-full", device.online ? "bg-emerald-500" : "bg-muted-foreground")} />
            {device.online ? t("sidebar.online") : t("sidebar.offline")}
          </Badge>
        </div>

        <div className="flex items-center justify-between gap-2">
          <span className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
            <Usb size={14} className={device.bootsel ? "text-amber-500" : undefined} />
            {t("sidebar.bootsel")}
          </span>
          <Switch
            checked={device.bootsel}
            label={device.bootsel ? t("sidebar.recovery") : t("sidebar.normal")}
            onCheckedChange={(v) => void handleBootsel(v)}
          />
        </div>

        <div className="flex items-center justify-between gap-2 rounded-lg bg-muted/60 px-2.5 py-2">
          <span className="flex items-center gap-2 text-xs text-muted-foreground">
            <Clock3 size={14} /> {t("sidebar.time")}
          </span>
          <Clock rtcDate={device.live.rtcDate} />
        </div>

        <div className="flex items-center justify-between">
          <div className="flex overflow-hidden rounded-md border border-border text-xs font-medium">
            {(["en", "de"] as const).map((l) => (
              <button
                key={l}
                onClick={() => setLang(l)}
                title={l === "en" ? "English" : "Deutsch"}
                className={`px-2 py-1 uppercase ${
                  lang === l ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"
                }`}
              >
                {l}
              </button>
            ))}
          </div>
          <button
            onClick={onToggleTheme}
            className="flex h-7 w-7 items-center justify-center rounded-md border border-border text-muted-foreground hover:text-foreground"
            title={t("sidebar.theme")}
          >
            {dark ? <Sun size={14} /> : <Moon size={14} />}
          </button>
        </div>
      </div>
    </aside>
  );
}
