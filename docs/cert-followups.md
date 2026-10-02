# Certificates — offene Tests & Punkte (Stand: Batch B / Umfang A fertig)

Batch B (Umfang A) ist implementiert und unit-verifiziert
(`cargo test` grün, `npm run build` grün): X.509-Import nach `CE<id>`,
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

## Batch B2 / CSR-Export (implementiert 01.10.,NICHTS davon hardware-getestet)

Umfang A (Import) ist oben dokumentiert; B2 ist gebaut und unit-verifiziert
(`cargo test` grün, `npm run build` grün), aber komplett ohne
Hardware-Gegenbeweis — alles unten steht aus.

Implementiert:
- On-Device-Signatur `00 68` (P2 `0x33` RSA / `0x73` EC, Raw-TBS, Karte
  hasht SHA-256 selbst; aus `cmd_signature.c` verifiziert).
- TBS-Aufbau (CN Pflicht, O/OU/C optional), DN-Validierung, TBS-Cap 1 KiB,
  Extended-APDU via `case4`.
- Pubkey-Quellen: CVC aus `CE<id>` (authoritativ) oder SPKI aus
  Generation-Record (wird seit 01.10. bei jeder RSA/EC-Generierung
  mitgeschnitten); sonst ehrliche Absage.
- SW-Mapping: 6982 → Login, 6985 → Signaturverbot/Purpose, 6A84 → Counter
  verbraucht. RSA + ECDSA nur; AES/EdDSA/XDH werden abgelehnt.
- UI: CSR-Button pro Key-Zeile → Subject-Dialog → Download
  `key-<id>.csr.pem`.

Noch per Hardware zu testen (Zertifikate/CSR):
1. **CSR mit CE-Zertifikat**: exportieren → `openssl req -noout -text
   -verify` (Signatur + Subject + SPKI prüfen).
2. **CSR ohne CE, mit Gen-Record** (diese App generiert): SPKI im CSR muss
   zum Key passen (gegen `export_pubkey`/CVC gegenprüfen, falls CE später
   importiert wird).
3. **Negativ-Fälle**: AES-Key → `RefusedCsr`; Key ohne CE/Record →
   `NoPubkey`; falscher DN (leere CN, 3-Buchstaben-C) → `BadSubject`;
   Key mit Signaturverbot (Purpose) → `SignNotAllowed`.
4. **Counter-Verbrauch**: Key mit kleinem Counter-Limit signieren → Counter
   sinkt; erschöpfter Counter → `CounterExhausted`.
5. **PIN-Flow**: ohne Session-PIN → 6982 → Login-Dialog → erneut.
6. **Full Loop (der eigentliche B2-Beweis)**: CSR → extern/CA signieren →
   per Import zurückschreiben → byte-identisch lesen + Liste zeigt X.509.
7. **Große Keys**: RSA-4096-CSR (Extended-APDU + 512-Byte-Signatur via
   61xx-Chaining) und EC-P521-CSR erzeugen + verifizieren.

Weiter offen (kein Slice geplant):
- CA-Import nach `CA<id>`, Signaturkettenprüfung/Chain-Trust.
- Ed25519/Ed448-CSR (P2 `0x70`, Raw-Signatur) und XDH-Ablehnungstext.
- SPKI-Capture für Keys, die vor dem 01.10. generiert wurden (kein Record,
  kein CE → Absage bleibt).

## Implementierungs-Referenzen (B2 zusätzlich)

- `src-tauri/src/device.rs`: `export_csr`, `build_tbs_csr`, `assemble_csr`,
  `parse_spki_pubkey`, `spki_from_cvc`, `gak_spki_hex`, `GenResult.spki_hex`,
  OID-Konstanten (`OID_SHA256_RSA`, `OID_ECDSA_SHA256_X509`, `OID_AT_*`).
- `src-tauri/src/lib.rs`: `export_csr` registriert.
- `src/lib/tauri.ts`: `exportCsr`, `GenResult.spki_hex`.
- `src/lib/genRecords.ts`: `GenRecord.spkiHex` (optional, Public Key only).
- `src/hooks/useDevice.ts`: `exportCsr`.
- `src/pages/Keys.tsx`: `CsrDialog`, CSR-Button, `spkiHex`-Persistenz.
  Format-Tooltip, Download-Naming.
