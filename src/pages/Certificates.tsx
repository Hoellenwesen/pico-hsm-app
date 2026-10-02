import { useCallback, useEffect, useRef, useState } from "react";
import { Award, Download, RefreshCw, Trash2, Upload } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/Card";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { NA } from "../components/ui/NA";
import { tauriApi, type CertEntry, type DeviceError } from "../lib/tauri";
import type { DeviceState } from "../hooks/useDevice";

export function Certificates({ device }: { device: DeviceState }) {
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
      const err = e as DeviceError;
      setEntries(null);
      setError(err.hint ? `${err.message}. ${err.hint}` : err.message || String(e));
    } finally {
      setBusy(false);
    }
  }, [reader]);

  useEffect(() => {
    void load();
  }, [load]);

  async function removeCert(fid: string) {
    if (!reader) return;
    setDeleting(true);
    try {
      const msg = await device.deleteCert(fid);
      toast.success("Certificate deleted", { description: msg });
      setConfirmFid(null);
      await load();
    } catch (e) {
      const err = e as DeviceError;
      if (err?.auth_required) {
        toast.info("Login required", { description: "Enter the User-PIN, then delete again." });
      } else {
        toast.error("Delete failed", { description: err.hint ? `${err.message}. ${err.hint}` : err.message });
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
      toast.success(isX509 ? "Certificate downloaded" : "Public key exported", {
        description: `${cert.fid} as PEM.`,
      });
    } catch (e) {
      const err = e as DeviceError;
      if (err?.auth_required) {
        toast.info("Login required", { description: "Enter the User-PIN, then try again." });
      } else {
        toast.error("Download failed", { description: err.hint ? `${err.message}. ${err.hint}` : err.message });
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
      toast.error("File too large", { description: "Certificates are capped at 8192 bytes." });
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
      toast.success("Certificate imported", { description: msg });
      setPendingImport(null);
      await load();
    } catch (e) {
      const err = e as DeviceError;
      if (err?.auth_required) {
        toast.info("Login required", { description: "Enter the User-PIN, then import again." });
      } else {
        toast.error("Import failed", { description: err.hint ? `${err.message}. ${err.hint}` : err.message });
      }
    } finally {
      setImporting(false);
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold tracking-tight">Certificates</h1>
          <p className="text-sm text-muted-foreground">
            Key certificates (device CVC or imported X.509) plus the CA store.
          </p>
        </div>
        <Button variant="outline" disabled={!device.online || busy} onClick={() => void load()}>
          <RefreshCw size={15} /> Refresh
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>
            <Award size={16} className="text-primary" /> Certificates
          </CardTitle>
          <CardDescription>
            {reader ? (
              <>
                Reader: <span className="font-mono">{reader}</span>
              </>
            ) : (
              "No board connected."
            )}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {!reader ? (
            <NA />
          ) : error ? (
            <p className="text-sm text-red-500">{error}</p>
          ) : entries === null ? (
            <p className="text-sm text-muted-foreground">{busy ? "Reading…" : "—"}</p>
          ) : entries.length === 0 ? (
            <p className="text-sm text-muted-foreground">No keys on the device — nothing to show certificates for.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-xs text-muted-foreground">
                    <th className="py-2 pr-4 font-medium">Key ID</th>
                    <th className="py-2 pr-4 font-medium">Label</th>
                    <th className="py-2 pr-4 font-medium">Certificate</th>
                    <th className="py-2 pr-4 font-medium">Type</th>
                    <th className="py-2 pr-4 font-medium">Size</th>
                    <th className="py-2 pr-4 font-medium">Actions</th>
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
                            title={`Stored as ${c.fid}${c.format !== "unknown" ? ` (${c.format.toUpperCase()})` : ""}`}
                          >
                            Stored
                          </Badge>
                        ) : (
                          <Badge variant="muted" title="This firmware generation stores no EE file for the key">
                            None stored
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
                                  ? `Download stored X.509 certificate ${c.fid} as PEM`
                                  : `Export public key of ${c.fid} as PEM`
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
                              title={`Import X.509 certificate onto key ${c.id} (${c.fid})`}
                              onClick={() => pickImportFile(c)}
                              className="rounded p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                            >
                              <Upload size={14} />
                            </button>
                          )}
                          {c.kind === "ca" && (
                            <button
                              title={`Delete CA certificate ${c.fid}`}
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
          title={`Delete certificate ${confirmFid}?`}
          description="Removes the standalone CA certificate. Key certificates are deleted with their key group in the Keys tab."
          confirmLabel="Delete"
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
          title={`Import certificate onto key ${pendingImport.entry.id}?`}
          description={
            pendingImport.entry.has_cert
              ? `Replaces the stored certificate ${pendingImport.entry.fid} with the selected file (${pendingImport.bytes.length} bytes). The previous content is restored if the write fails.`
              : `Stores the selected file (${pendingImport.bytes.length} bytes) as ${pendingImport.entry.fid}. The file must be an X.509 certificate for this key.`
          }
          confirmLabel="Import"
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
