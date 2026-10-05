import { useCallback, useEffect, useRef, useState } from "react";
import { Award, Download, RefreshCw, Trash2, Upload } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/Card";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { NA } from "../components/ui/NA";
import { tauriApi, type CertEntry, type DeviceError } from "../lib/tauri";
import { useLang } from "../lib/i18n/LangContext";
import type { DeviceState } from "../hooks/useDevice";

export function Certificates({ device }: { device: DeviceState }) {
  const { t, terr } = useLang();
  const [entries, setEntries] = useState<CertEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmFid, setConfirmFid] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [downloading, setDownloading] = useState<string | null>(null);
  const [pendingImport, setPendingImport] = useState<{ entry: CertEntry; bytes: number[] } | null>(null);
  const [importing, setImporting] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [importTarget, setImportTarget] = useState<CertEntry | null>(null);
  const reader = device.live.reader;

  const load = useCallback(async () => {
    if (!reader) {
      setEntries(null);
      setError(null);
      return;
    }
    setBusy(true);
    try {
      setEntries(await tauriApi.certs(reader));
      setError(null);
    } catch (e) {
      const text = terr(e as DeviceError);
      setEntries(null);
      setError(text.hint ? `${text.message}. ${text.hint}` : text.message);
    } finally {
      setBusy(false);
    }
  }, [reader, terr]);

  useEffect(() => {
    void load();
  }, [load]);

  async function removeCert(fid: string) {
    if (!reader) return;
    setDeleting(true);
    try {
      const msg = await device.deleteCert(fid);
      toast.success(t("certs.deleted"), { description: msg });
      setConfirmFid(null);
      await load();
    } catch (e) {
      const err = e as DeviceError;
      if (err?.auth_required) {
        toast.info(t("common.loginRequired"), { description: t("common.loginAgain") });
      } else {
        const text = terr(err);
        toast.error(t("certs.deleteFailed"), { description: text.hint ? `${text.message}. ${text.hint}` : text.message });
      }
    } finally {
      setDeleting(false);
    }
  }

  async function downloadPem(cert: CertEntry) {
    if (!reader || downloading) return;
    setDownloading(cert.fid);
    try {
      const isX509 = cert.format === "x509";
      const pem = isX509 ? await device.downloadCert(cert.fid) : await device.exportCert(cert.fid);
      const blob = new Blob([pem], { type: "application/x-pem-file" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = isX509 ? `${cert.fid}-cert.pem` : `${cert.fid}-pubkey.pem`;
      a.click();
      URL.revokeObjectURL(url);
      toast.success(isX509 ? t("certs.downloaded") : t("certs.exported"), {
        description: t("certs.asPem", { fid: cert.fid }),
      });
    } catch (e) {
      const err = e as DeviceError;
      if (err?.auth_required) {
        toast.info(t("common.loginRequired"), { description: t("common.loginAgain") });
      } else {
        const text = terr(err);
        toast.error(t("certs.downloadFailed"), { description: text.hint ? `${text.message}. ${text.hint}` : text.message });
      }
    } finally {
      setDownloading(null);
    }
  }

  function pickImportFile(entry: CertEntry) {
    setImportTarget(entry);
    // Reset so picking the same file twice still fires onChange.
    if (fileRef.current) fileRef.current.value = "";
    fileRef.current?.click();
  }

  async function onImportFile(file: File | undefined) {
    if (!file || !importTarget) return;
    if (file.size > 8192) {
      toast.error(t("certs.fileTooLarge"), { description: t("certs.fileTooLargeHint") });
      return;
    }
    const buf = await file.arrayBuffer();
    setPendingImport({ entry: importTarget, bytes: Array.from(new Uint8Array(buf)) });
  }

  async function runImport() {
    if (!pendingImport) return;
    setImporting(true);
    try {
      const msg = await device.importCert(pendingImport.entry.id, pendingImport.bytes);
      toast.success(t("certs.imported"), { description: msg });
      setPendingImport(null);
      await load();
    } catch (e) {
      const err = e as DeviceError;
      if (err?.auth_required) {
        toast.info(t("common.loginRequired"), { description: t("common.loginAgain") });
      } else {
        const text = terr(err);
        toast.error(t("certs.importFailed"), { description: text.hint ? `${text.message}. ${text.hint}` : text.message });
      }
    } finally {
      setImporting(false);
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold tracking-tight">{t("certs.title")}</h1>
          <p className="text-sm text-muted-foreground">
            {t("certs.subtitle")}
          </p>
        </div>
        <Button variant="outline" disabled={!device.online || busy} onClick={() => void load()}>
          <RefreshCw size={15} /> {t("common.refresh")}
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>
            <Award size={16} className="text-primary" /> {t("certs.title")}
          </CardTitle>
          <CardDescription>
            {reader ? (
              <>
                {t("certs.reader")} <span className="font-mono">{reader}</span>
              </>
            ) : (
              t("certs.noBoard")
            )}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {!reader ? (
            <NA />
          ) : error ? (
            <p className="text-sm text-red-500">{error}</p>
          ) : entries === null ? (
            <p className="text-sm text-muted-foreground">{busy ? t("common.loading") : t("common.none")}</p>
          ) : entries.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("certs.empty")}</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-xs text-muted-foreground">
                    <th className="py-2 pr-4 font-medium">{t("certs.colKeyId")}</th>
                    <th className="py-2 pr-4 font-medium">{t("certs.colLabel")}</th>
                    <th className="py-2 pr-4 font-medium">{t("certs.colCert")}</th>
                    <th className="py-2 pr-4 font-medium">{t("certs.colType")}</th>
                    <th className="py-2 pr-4 font-medium">{t("certs.colSize")}</th>
                    <th className="py-2 pr-4 font-medium">{t("certs.colActions")}</th>
                  </tr>
                </thead>
                <tbody>
                  {entries.map((c) => (
                    <tr key={c.fid} className="border-b border-border/50 last:border-0 hover:bg-muted/40">
                      <td className="py-2 pr-4 font-mono">{c.id}</td>
                      <td className="py-2 pr-4">{c.label ?? <NA />}</td>
                      <td className="py-2 pr-4">
                        {c.has_cert ? (
                          <Badge
                            variant="success"
                            title={t("certs.storedTitle", { fid: c.fid, format: c.format !== "unknown" ? ` (${c.format.toUpperCase()})` : "" })}
                          >
                            {t("certs.stored")}
                          </Badge>
                        ) : (
                          <Badge variant="muted" title={t("certs.noneStoredTitle")}>
                            {t("certs.noneStored")}
                          </Badge>
                        )}
                      </td>
                      <td className="py-2 pr-4">
                        {c.key_type === "unknown" ? (
                          <NA />
                        ) : (
                          <Badge variant="info">{c.key_type}</Badge>
                        )}
                      </td>
                      <td className="py-2 pr-4 font-mono">
                        {c.size_bits != null ? (
                          <>
                            {c.size_bits}
                            {c.curve ? <span className="text-muted-foreground"> · {c.curve}</span> : null}
                          </>
                        ) : (
                          <NA />
                        )}
                      </td>
                      <td className="py-2 pr-4">
                        <span className="flex items-center gap-1">
                          {c.has_cert ? (
                            <button
                              title={
                                c.format === "x509"
                                  ? t("certs.downloadX509", { fid: c.fid })
                                  : t("certs.downloadPubkey", { fid: c.fid })
                              }
                              disabled={downloading !== null}
                              onClick={() => void downloadPem(c)}
                              className="rounded p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-40"
                            >
                              <Download size={14} />
                            </button>
                          ) : null}
                          {c.kind === "ee" && c.id !== 0 && (
                            <button
                              title={t("certs.importTitle", { id: c.id, fid: c.fid })}
                              onClick={() => pickImportFile(c)}
                              className="rounded p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                            >
                              <Upload size={14} />
                            </button>
                          )}
                          {c.kind === "ca" && (
                            <button
                              title={t("certs.deleteCaTitle", { fid: c.fid })}
                              onClick={() => setConfirmFid(c.fid)}
                              className="rounded p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                            >
                              <Trash2 size={14} />
                            </button>
                          )}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {confirmFid !== null && (
        <ConfirmDialog
          title={t("certs.delTitle", { fid: confirmFid })}
          description={t("certs.delDesc")}
          confirmLabel={t("common.delete")}
          danger
          busy={deleting}
          onConfirm={() => void removeCert(confirmFid)}
          onCancel={() => {
            if (!deleting) setConfirmFid(null);
          }}
        />
      )}

      <input
        ref={fileRef}
        type="file"
        accept=".der,.pem,.crt,.cer"
        className="hidden"
        onChange={(e) => void onImportFile(e.target.files?.[0])}
      />
      {pendingImport !== null && (
        <ConfirmDialog
          title={t("certs.importAsk", { id: pendingImport.entry.id })}
          description={
            pendingImport.entry.has_cert
              ? t("certs.importReplace", { fid: pendingImport.entry.fid, bytes: pendingImport.bytes.length })
              : t("certs.importNew", { bytes: pendingImport.bytes.length, fid: pendingImport.entry.fid })
          }
          confirmLabel={t("certs.importConfirm")}
          danger={pendingImport.entry.has_cert}
          busy={importing}
          onConfirm={() => void runImport()}
          onCancel={() => {
            if (!importing) setPendingImport(null);
          }}
        />
      )}
    </div>
  );
}
