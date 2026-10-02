import { useEffect, useState } from "react";
import { Rocket, SlidersHorizontal } from "lucide-react";
import { toast } from "sonner";
import { Diagnose } from "../components/Diagnose";
import { PinInput, PinManagement } from "../components/PinManagement";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/Card";
import { ConfirmDialog } from "../components/ConfirmDialog";
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
      <InitializationCard device={device} />
      <Diagnose device={device} />
    </div>
  );
}

function InitializationCard({ device }: { device: DeviceState }) {
  const [userPin, setUserPin] = useState("");
  const [userRepeat, setUserRepeat] = useState("");
  const [soPin, setSoPin] = useState("");
  const [soRepeat, setSoRepeat] = useState("");
  const [retries, setRetries] = useState(3);
  const [dkek, setDkek] = useState<"none" | "slots">("none");
  const [slots, setSlots] = useState(1);
  const [wipeOk, setWipeOk] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  const userOk = userPin.length >= 6 && userPin.length <= 16 && userPin === userRepeat;
  const soOk = /^[0-9A-Fa-f]{16}$/.test(soPin) && soPin === soRepeat;
  const retriesOk = Number.isInteger(retries) && retries >= 1 && retries <= 15;
  const slotsOk = dkek !== "slots" || (Number.isInteger(slots) && slots >= 1 && slots <= 8);
  const canSubmit = device.online && !busy && userOk && soOk && retriesOk && slotsOk && wipeOk;
  const initialized = device.initState === "initialized";

  async function runInit() {
    if (!canSubmit) return;
    setConfirming(false);
    setBusy(true);
    try {
      const msg = await device.initializeDevice(
        userPin,
        soPin,
        retries,
        dkek === "slots" ? slots : null,
        false,
      );
      toast.success("Device initialized", {
        description:
          dkek === "slots"
            ? `${msg} Generate ${slots} share(s) in the Backup tab and import them to complete the domain.`
            : `${msg} Session unlocked.`,
      });
      setUserPin("");
      setUserRepeat("");
      setSoPin("");
      setSoRepeat("");
      setWipeOk(false);
    } catch (e) {
      const err = e as DeviceError;
      toast.error("Initialization failed", {
        description: err.hint ? `${err.message}. ${err.hint}` : err.message || String(e),
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <Rocket size={16} className="text-primary" /> Initialization
        </CardTitle>
        <CardDescription>
          First setup — or full wipe and re-setup.{" "}
          {device.initState === "uninitialized" ? (
            <Badge variant="warning">Not initialized</Badge>
          ) : device.initState === "initialized" ? (
            <Badge variant="success">Initialized</Badge>
          ) : (
            <NA />
          )}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {initialized && (
          <p className="text-sm text-amber-500">
            This device is initialized — running this erases all keys and starts over.
          </p>
        )}
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-2">
            <p className="text-xs text-muted-foreground">User-PIN (6–16 chars)</p>
            <PinInput value={userPin} onChange={setUserPin} placeholder="New User-PIN" />
            <PinInput value={userRepeat} onChange={setUserRepeat} placeholder="Repeat User-PIN" />
          </div>
          <div className="space-y-2">
            <p className="text-xs text-muted-foreground">SO-PIN (exactly 16 hex chars, same as change/unblock)</p>
            <PinInput value={soPin} onChange={setSoPin} placeholder="New SO-PIN (16 hex)" />
            <PinInput value={soRepeat} onChange={setSoRepeat} placeholder="Repeat SO-PIN" />
          </div>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block text-sm">
            <span className="mb-1 block text-xs text-muted-foreground">User-PIN retries (1–15)</span>
            <input
              type="number"
              min={1}
              max={15}
              value={retries}
              onChange={(e) => setRetries(Number(e.target.value))}
              className="h-9 w-28 rounded-lg border border-border bg-background px-3 text-sm outline-none focus:border-primary"
            />
          </label>
          <label className="block text-sm">
            <span className="mb-1 block text-xs text-muted-foreground">DKEK at setup</span>
            <select
              value={dkek}
              onChange={(e) => setDkek(e.target.value as "none" | "slots")}
              className="h-9 rounded-lg border border-border bg-background px-2 text-sm outline-none focus:border-primary"
            >
              <option value="none">None</option>
              <option value="slots">N empty slots (shares come from the Backup tab)</option>
            </select>
            {dkek === "slots" && (
              <input
                type="number"
                min={1}
                max={8}
                value={slots}
                onChange={(e) => setSlots(Number(e.target.value))}
                className="ml-2 h-9 w-20 rounded-lg border border-border bg-background px-3 text-sm outline-none focus:border-primary"
              />
            )}
          </label>
        </div>
        <label className="flex cursor-pointer items-start gap-2 text-sm">
          <input
            type="checkbox"
            checked={wipeOk}
            onChange={(e) => setWipeOk(e.target.checked)}
            className="mt-1 h-4 w-4 accent-current"
          />
          I understand this erases all keys and credentials on the device.
        </label>
        <Button variant="primary" disabled={!canSubmit} onClick={() => setConfirming(true)}>
          {busy ? "Initializing…" : "Initialize device"}
        </Button>
      </CardContent>

      {confirming && (
        <ConfirmDialog
          title="Initialize device and erase everything?"
          description="This wipes all keys, certificates and PINs, then sets up the device with the entered values. This cannot be undone without a DKEK backup."
          confirmLabel="Initialize now"
          danger
          busy={busy}
          onConfirm={() => void runInit()}
          onCancel={() => {
            if (!busy) setConfirming(false);
          }}
        />
      )}
    </Card>
  );
}
