import { useEffect, useState } from "react";
import { SlidersHorizontal } from "lucide-react";
import { toast } from "sonner";
import { Diagnose } from "../components/Diagnose";
import { PinManagement } from "../components/PinManagement";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/Card";
import { NA } from "../components/ui/NA";
import { Switch } from "../components/ui/Switch";
import type { DeviceError } from "../lib/tauri";
import type { DeviceState } from "../hooks/useDevice";

function OptionsCard({ device }: { device: DeviceState }) {
  const [press, setPress] = useState<boolean | null>(null);
  const [counter, setCounter] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (busy) return;
    setPress(device.securityOpts?.pressConfirm ?? null);
    setCounter(device.securityOpts?.keyCounter ?? null);
  }, [device.securityOpts, busy]);

  async function save() {
    if (press === null || counter === null || busy) return;
    setBusy(true);
    try {
      const msg = await device.saveDynops(press, counter);
      toast.success("Options updated", { description: msg });
    } catch (e) {
      const err = e as DeviceError;
      if (err?.auth_required) {
        toast.info("Login required", { description: "Enter the User-PIN, then save again." });
      } else {
        toast.error("Options update failed", { description: err.hint ? `${err.message}. ${err.hint}` : err.message });
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <SlidersHorizontal size={16} className="text-primary" /> Dynamic options
        </CardTitle>
        <CardDescription>
          Changeable without re-initialization. Saving needs User-PIN login. Secure-lock bit is never touched.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {press === null || counter === null ? (
          <NA />
        ) : (
          <>
            <div className="flex flex-wrap gap-x-6 gap-y-2">
              <Switch checked={press} onCheckedChange={setPress} label="Press-to-confirm button" />
              <Switch checked={counter} onCheckedChange={setCounter} label="Key usage counter for all keys" />
            </div>
            <div className="flex items-center gap-2 text-sm">
              <span className="text-muted-foreground">Secure lock:</span>
              {device.securityOpts ? (
                <Badge variant={device.securityOpts.secureLock ? "warning" : "outline"}>
                  {device.securityOpts.secureLock ? "ENABLED (read-only)" : "OFF"}
                </Badge>
              ) : (
                <NA />
              )}
              {!device.unlocked && (
                <span className="text-xs text-muted-foreground">(unlock with User-PIN to save)</span>
              )}
            </div>
            <Button variant="primary" disabled={!device.online || busy} onClick={() => void save()}>
              {busy ? "Saving…" : "Save options"}
            </Button>
          </>
        )}
      </CardContent>
    </Card>
  );
}

export function DeviceConfig({ device }: { device: DeviceState }) {
  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-bold tracking-tight">Device Config</h1>
        <p className="text-sm text-muted-foreground">Dynamic options and PIN management.</p>
      </div>

      <OptionsCard device={device} />
      <PinManagement device={device} />
      <Diagnose device={device} />
    </div>
  );
}
