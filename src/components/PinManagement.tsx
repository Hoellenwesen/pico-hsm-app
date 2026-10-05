import { useState } from "react";
import { Eye, EyeOff, KeyRound } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/Card";
import { NA } from "./ui/NA";
import { tauriApi, type DeviceError } from "../lib/tauri";
import { audit } from "../lib/auditLog";
import { useLang } from "../lib/i18n/LangContext";
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
  const { t } = useLang();
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
        title={show ? t("pin.hide") : t("pin.show")}
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
  titleKey,
  ruleKey,
  validateNew,
}: {
  device: DeviceState;
  pinRef: 0x81 | 0x88;
  titleKey: string;
  ruleKey: string;
  validateNew: (v: string) => string | null;
}) {
  const [oldPin, setOldPin] = useState("");
  const [newPin, setNewPin] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const { t, terr } = useLang();

  const mismatch = newPin !== "" && confirm !== "" && newPin !== confirm;
  const newErrKey = newPin === "" ? null : validateNew(newPin);
  const canSubmit = device.online && oldPin !== "" && newPin !== "" && confirm !== "" && !mismatch && !newErrKey && !busy;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit || !device.live.reader) return;
    setBusy(true);
    try {
      const msg = await tauriApi.changePin(device.live.reader, pinRef, oldPin, newPin);
      audit(pinRef === 0x81 ? "pin.change-user" : "pin.change-so", pinRef === 0x81 ? "User-PIN" : "SO-PIN", "ok");
      toast.success(t(titleKey), { description: msg });
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
      audit(
        pinRef === 0x81 ? "pin.change-user" : "pin.change-so",
        pinRef === 0x81 ? "User-PIN" : "SO-PIN",
        "error",
        (err as DeviceError)?.code,
      );
      const text = terr(err as DeviceError);
      errToast(t("pin.changeFailedTitle", { title: t(titleKey) }), { message: text.message, hint: text.hint } as DeviceError);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={(e) => void submit(e)} className="space-y-2 rounded-lg border border-border p-3">
      <p className="text-sm font-medium">{t(titleKey)}</p>
      <PinInput value={oldPin} onChange={setOldPin} placeholder={t("pin.curPin")} />
      <PinInput value={newPin} onChange={setNewPin} placeholder={t("pin.newPin")} />
      <PinInput value={confirm} onChange={setConfirm} placeholder={t("pin.confirmPin")} />
      <p className="text-xs text-muted-foreground">{t(ruleKey)}</p>
      {newErrKey && <p className="text-xs text-red-500">{t(newErrKey)}</p>}
      {mismatch && <p className="text-xs text-red-500">{t("pin.mismatch")}</p>}
      <Button variant="primary" type="submit" disabled={!canSubmit}>
        {busy ? t("pin.working") : t("pin.changeBtn")}
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
  const { t, terr } = useLang();
  const rrc = device.securityOpts?.resetRetryCounter;

  const mismatch = newPin !== "" && confirm !== "" && newPin !== confirm;
  const newErrKey = newPin === "" ? null : newPin.length < 6 || newPin.length > 16 ? "pin.userRule" : null;
  const soErrKey = sopin === "" ? null : !isHex16(sopin) ? "pin.soRule" : null;
  const canSubmit = device.online && rrc && !soErrKey && !newErrKey && sopin !== "" && newPin !== "" && confirm !== "" && !mismatch && !busy;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit || !device.live.reader) return;
    setBusy(true);
    try {
      const msg = await tauriApi.unblockPin(device.live.reader, sopin, newPin);
      audit("pin.unblock", "User-PIN via SO-PIN", "ok");
      toast.success(t("pin.unblocked"), { description: msg });
      setSopin("");
      setNewPin("");
      setConfirm("");
      device.refresh();
      // A new User-PIN was set — lock and ask for it.
      device.logout();
      device.requestPin();
    } catch (err) {
      audit("pin.unblock", "User-PIN via SO-PIN", "error", (err as DeviceError)?.code);
      const text = terr(err as DeviceError);
      errToast(t("pin.unblockFailed"), { message: text.message, hint: text.hint } as DeviceError);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={(e) => void submit(e)} className="space-y-2 rounded-lg border border-border p-3">
      <p className="text-sm font-medium">{t("pin.unblockTitle")}</p>
      {!device.online ? (
        <NA />
      ) : !rrc ? (
        <p className="text-xs text-amber-500">
          {t("pin.rrcOff")}
        </p>
      ) : null}
      <PinInput value={sopin} onChange={setSopin} placeholder={t("pin.soPh")} />
      <PinInput value={newPin} onChange={setNewPin} placeholder={t("pin.newUserPh")} />
      <PinInput value={confirm} onChange={setConfirm} placeholder={t("pin.confirmUserPh")} />
      <p className="text-xs text-muted-foreground">
        {t("pin.soNote")}
      </p>
      {soErrKey && <p className="text-xs text-red-500">{t(soErrKey)}</p>}
      {newErrKey && <p className="text-xs text-red-500">{t(newErrKey)}</p>}
      {mismatch && <p className="text-xs text-red-500">{t("pin.mismatch")}</p>}
      <Button variant="primary" type="submit" disabled={!canSubmit}>
        {busy ? t("pin.working") : t("pin.unblockBtn")}
      </Button>
    </form>
  );
}

export function PinManagement({ device }: { device: DeviceState }) {
  const { t } = useLang();
  const userTries = device.live.pin;
  const soTries = device.live.sopin;
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <KeyRound size={16} className="text-primary" /> {t("pin.title")}
        </CardTitle>
        <CardDescription>{t("pin.sub")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-muted-foreground">{t("pin.triesLeft")}</span>
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
          {device.live.pin?.blocked && <Badge variant="destructive">{t("pin.userBlocked")}</Badge>}
        </div>
        <div className="grid gap-4 lg:grid-cols-3">
          <ChangePinForm
            device={device}
            pinRef={0x81}
            titleKey="pin.changeUser"
            ruleKey="pin.userRule"
            validateNew={(v) => (v.length < 6 || v.length > 16 ? "pin.userRule" : null)}
          />
          <ChangePinForm
            device={device}
            pinRef={0x88}
            titleKey="pin.changeSo"
            ruleKey="pin.soRule"
            validateNew={(v) => (!isHex16(v) ? "pin.soRule" : null)}
          />
          <UnblockForm device={device} />
        </div>
      </CardContent>
    </Card>
  );
}
