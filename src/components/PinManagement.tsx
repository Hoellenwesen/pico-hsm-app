import { useState } from "react";
import { Eye, EyeOff, KeyRound } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/Card";
import { NA } from "./ui/NA";
import { tauriApi, type DeviceError } from "../lib/tauri";
import type { DeviceState } from "../hooks/useDevice";

export function PinInput({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
}) {
  const [show, setShow] = useState(false);
  return (
    <div className="relative">
      <input
        type={show ? "text" : "password"}
        autoComplete="off"
        value={value}
        onChange={(e) => onChange(e.currentTarget.value)}
        placeholder={placeholder}
        className="h-9 w-full rounded-lg border border-border bg-background px-3 pr-9 text-sm outline-none focus:border-primary"
      />
      <button
        type="button"
        tabIndex={-1}
        title={show ? "Hide" : "Show"}
        onClick={() => setShow((s) => !s)}
        className="absolute right-1 top-1 rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
      >
        {show ? <EyeOff size={14} /> : <Eye size={14} />}
      </button>
    </div>
  );
}

function errToast(title: string, e: unknown) {
  const err = e as DeviceError;
  // Never echo PIN values — backend messages contain only codes and counters.
  toast.error(title, { description: err?.hint ? `${err.message}. ${err.hint}` : err?.message || String(e) });
}

function ChangePinForm({
  device,
  pinRef,
  title,
  newPinRule,
  validateNew,
}: {
  device: DeviceState;
  pinRef: 0x81 | 0x88;
  title: string;
  newPinRule: string;
  validateNew: (v: string) => string | null;
}) {
  const [oldPin, setOldPin] = useState("");
  const [newPin, setNewPin] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);

  const mismatch = newPin !== "" && confirm !== "" && newPin !== confirm;
  const newErr = newPin === "" ? null : validateNew(newPin);
  const canSubmit = device.online && oldPin !== "" && newPin !== "" && confirm !== "" && !mismatch && !newErr && !busy;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit || !device.live.reader) return;
    setBusy(true);
    try {
      const msg = await tauriApi.changePin(device.live.reader, pinRef, oldPin, newPin);
      toast.success(title, { description: msg });
      setOldPin("");
      setNewPin("");
      setConfirm("");
      device.refresh();
      if (pinRef === 0x81) {
        // The stored session PIN is stale now — lock and ask for the new one.
        device.logout();
        device.requestPin();
      }
    } catch (err) {
      errToast(`${title} failed`, err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={(e) => void submit(e)} className="space-y-2 rounded-lg border border-border p-3">
      <p className="text-sm font-medium">{title}</p>
      <PinInput value={oldPin} onChange={setOldPin} placeholder="Current PIN" />
      <PinInput value={newPin} onChange={setNewPin} placeholder="New PIN" />
      <PinInput value={confirm} onChange={setConfirm} placeholder="Confirm new PIN" />
      <p className="text-xs text-muted-foreground">{newPinRule}</p>
      {newErr && <p className="text-xs text-red-500">{newErr}</p>}
      {mismatch && <p className="text-xs text-red-500">New PINs do not match.</p>}
      <Button variant="primary" type="submit" disabled={!canSubmit}>
        {busy ? "Working…" : "Change PIN"}
      </Button>
    </form>
  );
}

const isHex16 = (v: string) => /^[0-9a-fA-F]{16}$/.test(v);

function UnblockForm({ device }: { device: DeviceState }) {
  const [sopin, setSopin] = useState("");
  const [newPin, setNewPin] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const rrc = device.securityOpts?.resetRetryCounter;

  const mismatch = newPin !== "" && confirm !== "" && newPin !== confirm;
  const newErr = newPin === "" ? null : newPin.length < 6 || newPin.length > 16 ? "User-PIN: 6-16 characters." : null;
  const soErr = sopin === "" ? null : !isHex16(sopin) ? "SO-PIN: exactly 16 hex characters." : null;
  const canSubmit = device.online && rrc && !soErr && !newErr && sopin !== "" && newPin !== "" && confirm !== "" && !mismatch && !busy;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit || !device.live.reader) return;
    setBusy(true);
    try {
      const msg = await tauriApi.unblockPin(device.live.reader, sopin, newPin);
      toast.success("User-PIN unblocked", { description: msg });
      setSopin("");
      setNewPin("");
      setConfirm("");
      device.refresh();
      // A new User-PIN was set — lock and ask for it.
      device.logout();
      device.requestPin();
    } catch (err) {
      errToast("Unblock failed", err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={(e) => void submit(e)} className="space-y-2 rounded-lg border border-border p-3">
      <p className="text-sm font-medium">Unblock User-PIN</p>
      {!device.online ? (
        <NA />
      ) : !rrc ? (
        <p className="text-xs text-amber-500">
          Not allowed: the RESET RETRY COUNTER option bit is not enabled on this device.
        </p>
      ) : null}
      <PinInput value={sopin} onChange={setSopin} placeholder="SO-PIN (16 hex chars)" />
      <PinInput value={newPin} onChange={setNewPin} placeholder="New User-PIN" />
      <PinInput value={confirm} onChange={setConfirm} placeholder="Confirm new User-PIN" />
      <p className="text-xs text-muted-foreground">
        Needs the SO-PIN (15 tries). A wrong SO-PIN counts against the SO counter.
      </p>
      {soErr && <p className="text-xs text-red-500">{soErr}</p>}
      {newErr && <p className="text-xs text-red-500">{newErr}</p>}
      {mismatch && <p className="text-xs text-red-500">New PINs do not match.</p>}
      <Button variant="primary" type="submit" disabled={!canSubmit}>
        {busy ? "Working…" : "Unblock + set PIN"}
      </Button>
    </form>
  );
}

export function PinManagement({ device }: { device: DeviceState }) {
  const userTries = device.live.pin;
  const soTries = device.live.sopin;
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <KeyRound size={16} className="text-primary" /> PIN management
        </CardTitle>
        <CardDescription>Change and unblock PINs. Values stay in memory only, never on disk.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-muted-foreground">Tries left:</span>
          {userTries && userTries.retries >= 0 ? (
            <Badge variant="outline">UserPIN {userTries.retries}</Badge>
          ) : (
            <span>
              UserPIN <NA />
            </span>
          )}
          {soTries && soTries.retries >= 0 ? (
            <Badge variant="outline">SOPIN {soTries.retries}</Badge>
          ) : (
            <span>
              SOPIN <NA />
            </span>
          )}
          {device.live.pin?.blocked && <Badge variant="destructive">User-PIN blocked</Badge>}
        </div>
        <div className="grid gap-4 lg:grid-cols-3">
          <ChangePinForm
            device={device}
            pinRef={0x81}
            title="Change User-PIN"
            newPinRule="User-PIN: 6-16 characters."
            validateNew={(v) => (v.length < 6 || v.length > 16 ? "User-PIN: 6-16 characters." : null)}
          />
          <ChangePinForm
            device={device}
            pinRef={0x88}
            title="Change SO-PIN"
            newPinRule="SO-PIN: exactly 16 hex characters."
            validateNew={(v) => (!isHex16(v) ? "SO-PIN: exactly 16 hex characters." : null)}
          />
          <UnblockForm device={device} />
        </div>
      </CardContent>
    </Card>
  );
}
