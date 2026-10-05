import { useMemo, useState } from "react";
import { Download, ScrollText, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/Card";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { clearAudit, loadAudit } from "../lib/auditLog";
import { useLang } from "../lib/i18n/LangContext";

function fmtTime(ts: string, locale: string): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString(locale, { hour12: false });
}

export function Logs() {
  const { t, lang } = useLang();
  const [entries, setEntries] = useState(loadAudit);
  const [filter, setFilter] = useState<"all" | "ok" | "error">("all");
  const [query, setQuery] = useState("");
  const [confirming, setConfirming] = useState(false);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return entries.filter(
      (e) =>
        (filter === "all" || e.result === filter) &&
        (q === "" || `${e.action} ${e.detail} ${e.code ?? ""}`.toLowerCase().includes(q)),
    );
  }, [entries, filter, query]);

  function download() {
    const blob = new Blob([JSON.stringify(entries, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `picohsm-audit-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
    toast.success(t("logs.exported"), { description: t("logs.exportedHint", { count: entries.length }) });
  }

  function clear() {
    setConfirming(false);
    clearAudit();
    setEntries([]);
    toast.success(t("logs.cleared"));
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold tracking-tight">{t("logs.title")}</h1>
          <p className="text-sm text-muted-foreground">
            {t("logs.sub")}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={filter}
            onChange={(e) => setFilter(e.target.value as "all" | "ok" | "error")}
            className="h-8 rounded-md border border-border bg-background px-2 text-sm outline-none focus:border-primary"
          >
            <option value="all">{t("logs.all")}</option>
            <option value="ok">{t("logs.ok")}</option>
            <option value="error">{t("logs.errors")}</option>
          </select>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("logs.searchPh")}
            className="h-8 w-40 rounded-md border border-border bg-background px-2 text-sm outline-none focus:border-primary"
          />
          <Button variant="outline" disabled={entries.length === 0} onClick={download}>
            <Download size={15} /> {t("logs.export")}
          </Button>
          <Button variant="outline" disabled={entries.length === 0} onClick={() => setConfirming(true)}>
            <Trash2 size={15} /> {t("logs.clear")}
          </Button>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>
            <ScrollText size={16} className="text-primary" /> {t("logs.journal")}
          </CardTitle>
          <CardDescription>
            {entries.length === 0
              ? t("logs.empty")
              : t("logs.count", { shown: shown.length, total: entries.length })}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {shown.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {entries.length === 0 ? t("logs.none") : t("logs.noMatch")}
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-xs text-muted-foreground">
                    <th className="py-2 pr-4 font-medium">{t("logs.colTime")}</th>
                    <th className="py-2 pr-4 font-medium">{t("logs.colAction")}</th>
                    <th className="py-2 pr-4 font-medium">{t("logs.colDetail")}</th>
                    <th className="py-2 pr-4 font-medium">{t("logs.colResult")}</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((e, i) => {
                    const actionKey = `log.action.${e.action}`;
                    const actionLabel = t(actionKey);
                    return (
                    <tr key={`${e.ts}-${i}`} className="border-b border-border/50 last:border-0 hover:bg-muted/40">
                      <td className="whitespace-nowrap py-2 pr-4 font-mono text-xs">{fmtTime(e.ts, lang)}</td>
                      <td className="py-2 pr-4 font-mono text-xs" title={e.action}>{actionLabel === actionKey ? e.action : actionLabel}</td>
                      <td className="py-2 pr-4">{e.detail}</td>
                      <td className="py-2 pr-4">
                        {e.result === "ok" ? (
                          <Badge variant="success">{t("logs.ok")}</Badge>
                        ) : (
                          <Badge variant="destructive" title={e.code ?? "error"}>
                            {t("logs.errors")}{e.code ? `: ${e.code}` : ""}
                          </Badge>
                        )}
                      </td>
                    </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {confirming && (
        <ConfirmDialog
          title={t("logs.clearTitle")}
          description={t("logs.clearDesc")}
          confirmLabel={t("logs.clearBtn")}
          danger
          busy={false}
          onConfirm={clear}
          onCancel={() => setConfirming(false)}
        />
      )}
    </div>
  );
}
