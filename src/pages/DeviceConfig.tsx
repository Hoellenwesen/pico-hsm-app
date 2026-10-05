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
import { useLang } from "../lib/i18n/LangContext";
import type { DeviceError } from "../lib/tauri";
import type { DeviceState } from "../hooks/useDevice";

function OptionsCard({ device }: { device: DeviceState }) {
  const [press, setPress] = useState<boolean | null>(null);
  const [counter, setCounter] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const { t, terr } = useLang();

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
      toast.success(t("dev.optionsUpdated"), { description: msg });
    } catch (e) {
      const err = e as DeviceError;
      if (err?.auth_required) {
        toast.info(t("common.loginRequired"), { description: t("common.loginAgain") });
      } else {
        const text = terr(err);
        toast.error(t("dev.optionsFailed"), { description: text.hint ? `${text.message}. ${text.hint}` : text.message });
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <SlidersHorizontal size={16} className="text-primary" /> {t("dev.optionsTitle")}
        </CardTitle>
        <CardDescription>
          {t("dev.optionsSub")}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {press === null || counter === null ? (
          <NA />
        ) : (
          <>
            <div className="flex flex-wrap gap-x-6 gap-y-2">
              <Switch checked={press} onCheckedChange={setPress} label={t("dev.pressConfirm")} />
              <Switch checked={counter} onCheckedChange={setCounter} label={t("dev.keyCounter")} />
            </div>
            <div className="flex items-center gap-2 text-sm">
              <span className="text-muted-foreground">{t("dev.secureLock")}</span>
              {device.securityOpts ? (
                <Badge variant={device.securityOpts.secureLock ? "warning" : "outline"}>
                  {device.securityOpts.secureLock ? t("dev.secureLockOn") : t("dev.secureOff")}
                </Badge>
              ) : (
                <NA />
              )}
              {!device.unlocked && (
                <span className="text-xs text-muted-foreground">{t("dev.unlockHint")}</span>
              )}
            </div>
            <Button variant="primary" disabled={!device.online || busy} onClick={() => void save()}>
              {busy ? t("dev.saving") : t("dev.saveOptions")}
            </Button>
          </>
        )}
      </CardContent>
    </Card>
  );
}

export function DeviceConfig({ device }: { device: DeviceState }) {
  const { t } = useLang();
  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-bold tracking-tight">{t("dev.title")}</h1>
        <p className="text-sm text-muted-foreground">{t("dev.sub")}</p>
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
  const { t, terr } = useLang();

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
      toast.success(t("dev.initialized"), {
        description:
          dkek === "slots"
            ? `${msg} ${t("dev.initSharesNext", { n: slots })}`
            : `${msg} ${t("dev.sessionUnlocked")}`,
      });
      setUserPin("");
      setUserRepeat("");
      setSoPin("");
      setSoRepeat("");
      setWipeOk(false);
    } catch (e) {
      const text = terr(e as DeviceError);
      toast.error(t("dev.initFailed"), {
        description: text.hint ? `${text.message}. ${text.hint}` : text.message,
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <Rocket size={16} className="text-primary" /> {t("dev.initTitle")}
        </CardTitle>
        <CardDescription>
          {t("dev.initSub")}{" "}
          {device.initState === "uninitialized" ? (
            <Badge variant="warning">{t("dev.initNotInit")}</Badge>
          ) : device.initState === "initialized" ? (
            <Badge variant="success">{t("dev.initDone")}</Badge>
          ) : (
            <NA />
          )}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {initialized && (
          <p className="text-sm text-amber-500">
            {t("dev.initWarnInit")}
          </p>
        )}
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-2">
            <p className="text-xs text-muted-foreground">{t("dev.userPin")}</p>
            <PinInput value={userPin} onChange={setUserPin} placeholder={t("dev.newUserPin")} />
            <PinInput value={userRepeat} onChange={setUserRepeat} placeholder={t("dev.repeatUserPin")} />
          </div>
          <div className="space-y-2">
            <p className="text-xs text-muted-foreground">{t("dev.soPin")}</p>
            <PinInput value={soPin} onChange={setSoPin} placeholder={t("dev.newSoPin")} />
            <PinInput value={soRepeat} onChange={setSoRepeat} placeholder={t("dev.repeatSoPin")} />
          </div>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block text-sm">
            <span className="mb-1 block text-xs text-muted-foreground">{t("dev.retries")}</span>
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
            <span className="mb-1 block text-xs text-muted-foreground">{t("dev.dkekSetup")}</span>
            <select
              value={dkek}
              onChange={(e) => setDkek(e.target.value as "none" | "slots")}
              className="h-9 rounded-lg border border-border bg-background px-2 text-sm outline-none focus:border-primary"
            >
              <option value="none">{t("dev.dkekNone")}</option>
              <option value="slots">{t("dev.dkekSlots")}</option>
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
          {t("dev.wipeCheck")}
        </label>
        <Button variant="primary" disabled={!canSubmit} onClick={() => setConfirming(true)}>
          {busy ? t("dev.initializing") : t("dev.initBtn")}
        </Button>
      </CardContent>

      {confirming && (
        <ConfirmDialog
          title={t("dev.initConfirmTitle")}
          description={t("dev.initConfirmDesc")}
          confirmLabel={t("dev.initNow")}
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
