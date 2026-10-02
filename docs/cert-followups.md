# Certificates — offene Tests & Punkte (Stand: Batch B / Umfang A fertig)

Batch B (Umfang A) ist implementiert und unit-verifiziert
(`cargo test` 16/16, `npm run build` grün): X.509-Import nach `CE<id>`,
Strukturvalidierung, Key-Match, Readback-Verify, Roh-Download, UI-Flow.

## Noch per Hardware zu testen (Zertifikate)

1. **Import-Roundtrip auf Scratch-Key-ID**
   - X.509 importieren → Liste zeigt `Stored` + Format `X.509` + plausible
     Typ/Größe/Kurve → Zertifikat downloaden → Bytes mit Input vergleichen
     (byte-identisch erwartet) → optional `openssl x509 -noout -text`.
2. **Protokoll-Proof UPDATE-auf-fehlende/gelöschte `CE`-Datei**
   - Unbelegt, ob UPDATE eine gelöschte/fehlende `CE`-Datei neu erzeugt
     (Import auf „None stored“-Keys). Falls 6A82 o.ä.: Create-Pfad klären.
3. **Key-Mismatch ablehnen**: Zertifikat eines anderen Keys importieren →
   `CertKeyMismatch` erwartet, nichts geschrieben.
4. **Überschreib-Restore**: Import mit bewusst defekter Datei nach gültigem
   Import (sofern möglich) → alter Inhalt muss wiederhergestellt sein.
5. **No-op**: identische Datei erneut importieren → „already stores …
   nothing changed“, kein Rewrite.
6. **PIN-Flow**: ohne Session-PIN importieren → 6982 → Login-Dialog →
   danach Import wiederholen.
7. **Modal/ESC/Backdrop** am Import-Confirm prüfen; danach Test-Keygruppe
   sauber löschen (Keys-Tab).

## Späterer Slice: Batch B2 / CSR-Export

- Für den vollen Loop „Key generieren → extern signieren → zurückschreiben“
  fehlt ein **CSR/PKCS#10-Export** für Device-Keys (Umfang A enthält ihn
  bewusst nicht; kein Chain-Trust, keine CA-Validierung).
- Ebenfalls offen gelassen: CA-Import nach `CA<id>`, Signaturkettenprüfung.

## Implementierungs-Referenzen

- `src-tauri/src/device.rs`: `parse_x509_pubkey`, `extract_der_cert`,
  `import_cert`, `export_cert`, `cert_blob_info`, `update_payload`, Tests.
- `src-tauri/src/lib.rs`: `import_cert`, `export_cert` registriert.
- `src/lib/tauri.ts`: `importCert`, `downloadCert`, `CertEntry.format`.
- `src/hooks/useDevice.ts`: `importCert`, `downloadCert`.
- `src/pages/Certificates.tsx`: Import-Button, Datei-Dialog, Confirm,
  Format-Tooltip, Download-Naming.
