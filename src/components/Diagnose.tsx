import { useState } from "react";
import { ChevronDown, Copy, Send, Wrench } from "lucide-react";
import { toast } from "sonner";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/Card";
import { NA } from "./ui/NA";
import { tauriApi, type DeviceError, type TransmitResult } from "../lib/tauri";
import { audit } from "../lib/auditLog";
import { useLang } from "../lib/i18n/LangContext";
import type { DeviceState } from "../hooks/useDevice";
import type { PinStatus } from "../lib/tauri";

function pinText(pin: PinStatus | null): string {
  if (!pin) return "N/A";
  if (pin.blocked) return "blocked";
  if (pin.retries < 0) return "not required";
  return `${pin.retries} left`;
}

/** Raw device values + field errors for troubleshooting. Collapsed by default. */
export function Diagnose({ device }: { device: DeviceState }) {
  const { t, terr } = useLang();
  const [open, setOpen] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [apdu, setApdu] = useState("00B1C402045402000000");
  const [applet, setApplet] = useState("hsm");
  const [sending, setSending] = useState(false);
  const [trace, setTrace] = useState<{ sent: string; resp: TransmitResult | string }[]>([]);

  async function send() {
    if (!device.live.reader || sending || apdu.trim() === "") return;
    setSending(true);
    // Journal the raw APDU shape only (applet + INS + length): the payload
    // may carry secrets (e.g. a VERIFY PIN), so it must never be logged.
    const clean = apdu.trim().replace(/\s+/g, "").toUpperCase();
    const ins = clean.length >= 4 ? clean.slice(2, 4) : "??";
    try {
      const resp = await tauriApi.transmitHsm(device.live.reader, apdu.trim(), applet);
      audit("diagnose.raw-apdu", `[${applet}] INS ${ins} (${clean.length / 2} bytes)`, "ok");
      setTrace((t) => [...t.slice(-9), { sent: `[${applet}] ${apdu.trim().toUpperCase()}`, resp }]);
    } catch (e) {
      const err = e as DeviceError;
      audit("diagnose.raw-apdu", `[${applet}] INS ${ins}`, "error", err?.code);
      setTrace((t) => [...t.slice(-9), { sent: `[${applet}] ${apdu.trim().toUpperCase()}`, resp: String((e as { message?: string })?.message ?? e) }]);
    } finally {
      setSending(false);
    }
  }

  return (
    <Card>
      <div
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setOpen((o) => !o);
          }
        }}
        className="cursor-pointer rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-primary"
      >
        <CardHeader>
          <CardTitle>
            <Wrench size={16} className="text-muted-foreground" /> {t("diag.title")}
            <ChevronDown
              size={16}
              className={`ml-auto text-muted-foreground transition-transform ${open ? "rotate-180" : ""}`}
            />
          </CardTitle>
          <CardDescription>{t("diag.sub")}</CardDescription>
        </CardHeader>
      </div>
      {open && (
        <CardContent className="space-y-3">
        <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
          <span className="text-muted-foreground">
            Reader:{" "}
            {device.live.reader ? (
              <span className="font-medium text-foreground">{device.live.reader}</span>
            ) : (
              <NA />
            )}
          </span>
          <span className="text-muted-foreground">
            Protocol:{" "}
            {device.live.protocol ? (
              <span className="font-medium text-foreground">{device.live.protocol}</span>
            ) : (
              <NA />
            )}
          </span>
          <span className="text-muted-foreground">
            Device RTC:{" "}
            {device.live.rtc ? (
              <span className="font-mono font-medium text-foreground">{device.live.rtc.display}</span>
            ) : (
              <NA />
            )}
            {device.online && (
              <button
                className="ml-2 rounded border border-border px-2 py-0.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-40"
                title="Set the device clock to this computer's local time"
                disabled={syncing}
                onClick={async () => {
                  if (syncing) return;
                  setSyncing(true);
                  try {
                    const now = new Date();
                    const msg = await device.syncClock({
                      year: now.getFullYear(),
                      month: now.getMonth() + 1,
                      day: now.getDate(),
                      weekday: now.getDay(),
                      hour: now.getHours(),
                      minute: now.getMinutes(),
                      second: now.getSeconds(),
                    });
                    toast.success(t("diag.synced"), { description: msg });
                  } catch (e) {
                    const text = terr(e as DeviceError);
                    toast.error(t("diag.syncFailed"), { description: text.hint ? `${text.message}. ${text.hint}` : text.message });
                  } finally {
                    setSyncing(false);
                  }
                }}
              >
                {syncing ? t("diag.syncing") : t("diag.sync")}
              </button>
            )}
          </span>
          <span className="text-muted-foreground">
            {t("diag.firmware")}{" "}
            {device.live.platform ? (
              <span className="font-mono font-medium text-foreground">v{device.live.platform.version} (rescue)</span>
            ) : device.live.version ? (
              <span className="font-mono font-medium text-foreground">
                v{device.live.version.display} (opts {device.live.version.opts_hex})
              </span>
            ) : (
              <NA />
            )}
          </span>
          <span className="text-muted-foreground">
            {t("diag.boardId")}{" "}
            {device.live.platform ? (
              <span className="font-mono font-medium text-foreground">{device.live.platform.board_hex}</span>
            ) : (
              <NA />
            )}
          </span>
          <span className="text-muted-foreground">
            {t("diag.pinRetries")}{" "}
            {device.live.pin || device.live.sopin ? (
              <span className="font-mono font-medium text-foreground">
                user {pinText(device.live.pin)} · so {pinText(device.live.sopin)}
              </span>
            ) : (
              <NA />
            )}
          </span>
          <span className="text-muted-foreground">
            {t("diag.memoryRaw")}{" "}
            {device.live.flash ? (
              <span className="font-mono font-medium text-foreground">{device.live.flash.raw_hex}</span>
            ) : (
              <NA />
            )}
          </span>
            {(device.live.fieldErrors.rtc ||
              device.live.fieldErrors.flash ||
              device.live.fieldErrors.secure ||
              device.live.fieldErrors.version ||
            device.live.fieldErrors.serial ||
            device.live.fieldErrors.pin ||
            device.live.fieldErrors.sopin ||
            device.live.fieldErrors.platform) && (
            <span className="text-muted-foreground">
              {t("diag.fieldErrors")}{" "}
              <span className="font-mono font-medium text-amber-500">
                {[
                  device.live.fieldErrors.rtc && `rtc(${device.live.fieldErrors.rtc})`,
                  device.live.fieldErrors.platform && `platform(${device.live.fieldErrors.platform})`,
                  device.live.fieldErrors.version && `version(${device.live.fieldErrors.version})`,
                    device.live.fieldErrors.flash && `flash(${device.live.fieldErrors.flash})`,
                    device.live.fieldErrors.secure && `secure(${device.live.fieldErrors.secure})`,
                  device.live.fieldErrors.serial && `serial(${device.live.fieldErrors.serial})`,
                  device.live.fieldErrors.pin && `pin(${device.live.fieldErrors.pin})`,
                  device.live.fieldErrors.sopin && `sopin(${device.live.fieldErrors.sopin})`,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
            </span>
          )}
        </div>
        <div>
          <div className="mb-1 flex items-center justify-between">
            <span className="text-xs font-medium text-muted-foreground">{t("diag.atr")}</span>
            {device.live.atrHex && (
              <button
                className="flex items-center gap-1 rounded p-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
                title={t("diag.copyAtr")}
                onClick={async () => {
                  try {
                    await navigator.clipboard.writeText(device.live.atrHex ?? "");
                    toast.success(t("diag.atrCopied"));
                  } catch {
                    toast.info("ATR", { description: device.live.atrHex ?? "" });
                  }
                }}
              >
                <Copy size={13} /> {t("diag.copy")}
              </button>
            )}
          </div>
          {device.live.atrHex ? (
            <p className="break-all rounded-lg bg-muted/60 p-3 font-mono text-xs leading-relaxed">
              {device.live.atrHex}
            </p>
          ) : (
            <div className="rounded-lg bg-muted/60 p-3">
              <NA />
            </div>
          )}
        </div>
        <div>
          <div className="mb-1 flex items-center justify-between">
            <span className="text-xs font-medium text-muted-foreground">
              {t("diag.rawTitle")}
            </span>
            {trace.length > 0 && (
              <button
                className="rounded p-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
                onClick={() => setTrace([])}
              >
                {t("diag.clear")}
              </button>
            )}
          </div>
          <div className="flex gap-2">
            <select
              value={applet}
              disabled={!device.online || sending}
              onChange={(e) => setApplet(e.currentTarget.value)}
              title={t("diag.appletTitle")}
              className="h-9 shrink-0 rounded-lg border border-border bg-background px-2 font-mono text-xs outline-none focus:border-primary"
            >
              <option value="hsm">HSM</option>
              <option value="rescue">Rescue</option>
            </select>
            <input
              value={apdu}
              disabled={!device.online || sending}
              onChange={(e) => setApdu(e.currentTarget.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void send();
              }}
              placeholder={t("diag.apduPh")}
              spellCheck={false}
              className="h-9 min-w-0 flex-1 rounded-lg border border-border bg-background px-3 font-mono text-xs outline-none focus:border-primary"
            />
            <button
              title={t("diag.sendTitle")}
              disabled={!device.online || sending || apdu.trim() === ""}
              onClick={() => void send()}
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground disabled:opacity-50"
            >
              <Send size={14} />
            </button>
          </div>
          {trace.length > 0 && (
            <div className="mt-2 max-h-48 space-y-1 overflow-y-auto rounded-lg bg-muted/60 p-3 font-mono text-xs leading-relaxed">
              {trace.map((tr, i) => (
                <div key={i} className="break-all">
                  <div className="text-sky-500">→ {tr.sent}</div>
                  {typeof tr.resp === "string" ? (
                    <div className="text-red-500">✕ {tr.resp}</div>
                  ) : (
                    <>
                      <div className="break-all text-foreground">{tr.resp.data_hex === "" ? t("diag.noData") : tr.resp.data_hex}</div>
                      <div className={tr.resp.sw_hex === "9000" ? "text-emerald-500" : "text-amber-500"}>
                        SW={tr.resp.sw_hex}
                      </div>
                    </>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </CardContent>
      )}
    </Card>
  );
}
