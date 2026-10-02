import { useEffect, useState } from "react";
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
import type { DeviceState } from "../../hooks/useDevice";
import { Switch } from "../ui/Switch";
import { Badge } from "../ui/Badge";

export type TabId = "dashboard" | "keys" | "certs" | "backup" | "device" | "firmware" | "logs";

export const tabs: { id: TabId; label: string; icon: typeof LayoutDashboard }[] = [
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard },
  { id: "keys", label: "Keys", icon: KeyRound },
  { id: "certs", label: "Certificates", icon: Award },
  { id: "backup", label: "Backup & Restore", icon: Archive },
  { id: "device", label: "Device Config", icon: Cpu },
  { id: "firmware", label: "Firmware", icon: HardDriveDownload },
  { id: "logs", label: "Logs", icon: ScrollText },
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
  useEffect(() => {
    if (!armed) return;
    const t = setTimeout(() => setArmed(false), 6000);
    return () => clearTimeout(t);
  }, [armed]);

  async function handleBootsel(v: boolean) {
    if (rebooting) return;
    if (!v) {
      setArmed(false);
      device.setBootsel(false);
      return;
    }
    if (!device.online) {
      toast.error("No device connected", { description: "Connect the Pico HSM first." });
      return;
    }
    if (!armed) {
      setArmed(true);
      toast.warning("Confirm reboot to BOOTSEL", {
        description: "Toggle again within 6 s to reboot. Press the device button when it asks, then the device will disconnect.",
      });
      return;
    }
    setArmed(false);
    setRebooting(true);
    try {
      const msg = await device.rebootDevice(true);
      device.setBootsel(true);
      toast.warning("Rebooting into BOOTSEL", { description: msg });
    } catch (e) {
      const err = e as { code?: string; message?: string; hint?: string };
      toast.error(err.code === "RebootUnsupported" ? "Reboot not supported here" : "Reboot failed", {
        description: err.hint || err.message || String(e),
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
        {tabs.map((t) => {
          const Icon = t.icon;
          const isActive = t.id === active;
          return (
            <button
              key={t.id}
              onClick={() => onNavigate(t.id)}
              className={cn(
                "flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors",
                isActive ? "bg-primary text-primary-foreground shadow-sm" : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <Icon size={17} />
              {t.label}
            </button>
          );
        })}
      </nav>

      <div className="space-y-3 border-t border-border p-4">
        <div className="flex items-center justify-between">
          <span className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
            {device.online ? <Wifi size={14} className="text-emerald-500" /> : <WifiOff size={14} className="text-muted-foreground" />}
            Device
          </span>
          <Badge variant={device.online ? "success" : "outline"}>
            <span className={cn("h-1.5 w-1.5 rounded-full", device.online ? "bg-emerald-500" : "bg-muted-foreground")} />
            {device.online ? "Online" : "Offline"}
          </Badge>
        </div>

        <div className="flex items-center justify-between gap-2">
          <span className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
            <Usb size={14} className={device.bootsel ? "text-amber-500" : undefined} />
            BOOTSEL
          </span>
          <Switch
            checked={device.bootsel}
            label={device.bootsel ? "Recovery" : "Normal"}
            onCheckedChange={(v) => void handleBootsel(v)}
          />
        </div>

        <div className="flex items-center justify-between gap-2 rounded-lg bg-muted/60 px-2.5 py-2">
          <span className="flex items-center gap-2 text-xs text-muted-foreground">
            <Clock3 size={14} /> Time
          </span>
          <span className="font-mono text-xs font-medium tabular-nums">{device.now ? formatClock(device.now) : "--"}</span>
        </div>

        <div className="flex items-center justify-end">
          <button
            onClick={onToggleTheme}
            className="flex h-7 w-7 items-center justify-center rounded-md border border-border text-muted-foreground hover:text-foreground"
            title="Toggle theme"
          >
            {dark ? <Sun size={14} /> : <Moon size={14} />}
          </button>
        </div>
      </div>
    </aside>
  );
}
