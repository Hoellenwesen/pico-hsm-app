import { Button } from "./ui/Button";
import { CardContent, CardDescription, CardHeader, CardTitle } from "./ui/Card";
import { ModalCancel, ModalShell } from "./ui/ModalShell";

/**
 * Generic OK/Cancel modal. Destructive actions pass a red confirm button;
 * long-running confirms stay open with `busy` + custom children content.
 * ESC, backdrop click and Cancel all close without action (blocked while busy).
 */
export function ConfirmDialog({
  title,
  description,
  confirmLabel = "Confirm",
  danger = false,
  busy = false,
  onConfirm,
  onCancel,
  children,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  confirmLabel?: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  children?: React.ReactNode;
}) {
  return (
    <ModalShell onCancel={onCancel} busy={busy}>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        {description && <CardDescription>{description}</CardDescription>}
      </CardHeader>
      <CardContent className="space-y-3">
        {children}
        <div className="flex gap-2">
          <Button
            variant="primary"
            disabled={busy}
            onClick={onConfirm}
            className={danger ? "bg-red-600 text-white hover:bg-red-600/90" : undefined}
          >
            {busy ? "Working…" : confirmLabel}
          </Button>
          <ModalCancel onCancel={onCancel} busy={busy} />
        </div>
      </CardContent>
    </ModalShell>
  );
}
