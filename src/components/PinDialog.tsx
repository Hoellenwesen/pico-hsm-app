import { useEffect, useState } from "react";
import { KeyRound } from "lucide-react";
import { toast } from "sonner";
import { Button } from "./ui/Button";
import { CardContent, CardDescription, CardHeader, CardTitle } from "./ui/Card";
import { ModalCancel, ModalShell } from "./ui/ModalShell";
import type { DeviceState } from "../hooks/useDevice";

/**
 * User-PIN prompt. The PIN lives in React state only (see useDevice):
 * never persisted, never logged, cleared on disconnect.
 */
export function PinDialog({ device }: { device: DeviceState }) {
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (device.pinRequired) setPin("");
  }, [device.pinRequired]);

  if (!device.pinRequired || !device.online) return null;

  async function submit(e?: React.FormEvent) {
    e?.preventDefault();
    if (!pin || busy) return;
    setBusy(true);
    try {
      const msg = await device.login(pin);
      toast.success("Unlocked", { description: msg });
    } catch (err) {
      const e = err as { code?: string; message?: string; hint?: string };
      // Wrong PIN shows retries left; anything else a generic error.
      // The PIN itself is never echoed.
      toast.error(e.code === "WrongPin" ? "Wrong PIN" : "Login failed", {
        description: e.hint ? `${e.message}. ${e.hint}` : e.message || String(err),
      });
    } finally {
      setBusy(false);
      setPin("");
    }
  }

  return (
    <ModalShell onCancel={() => device.dismissPin()} busy={busy}>
        <CardHeader>
          <CardTitle>
            <KeyRound size={16} className="text-primary" /> Enter User-PIN
          </CardTitle>
          <CardDescription>
            Required for protected operations (e.g. clock). The PIN stays in memory only and is cleared on
            disconnect. The device stays unlocked until unplugged.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={(e) => void submit(e)} className="flex gap-2">
            <input
              type="password"
              autoFocus
              autoComplete="off"
              value={pin}
              disabled={busy}
              onChange={(e) => setPin(e.currentTarget.value)}
              placeholder="User-PIN"
              className="h-9 min-w-0 flex-1 rounded-lg border border-border bg-background px-3 text-sm outline-none focus:border-primary"
            />
            <Button variant="primary" type="submit" disabled={!pin || busy}>
              Unlock
            </Button>
            <ModalCancel onCancel={() => device.dismissPin()} busy={busy} />
          </form>
        </CardContent>
    </ModalShell>
  );
}
