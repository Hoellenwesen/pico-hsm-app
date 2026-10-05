import { useEffect } from "react";
import { Button } from "./Button";
import { Card } from "./Card";
import { useLang } from "../../lib/i18n/LangContext";

/**
 * Shared modal shell: backdrop click and ESC close without action
 * (both disabled while `busy` so no operation is aborted halfway).
 * Every popup uses this — one look, one behavior.
 */
export function ModalShell({
  onCancel,
  busy = false,
  wide = false,
  children,
}: {
  onCancel: () => void;
  busy?: boolean;
  wide?: boolean;
  children: React.ReactNode;
}) {
  useEffect(() => {
    if (busy) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onCancel();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onCancel]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={() => {
        if (!busy) onCancel();
      }}
    >
      <Card
        className={wide ? "w-full max-w-lg" : "w-full max-w-md"}
        onClick={(e) => e.stopPropagation()}
      >
        {children}
      </Card>
    </div>
  );
}

/** Standard no-action footer button for all popups. */
export function ModalCancel({ onCancel, busy = false }: { onCancel: () => void; busy?: boolean }) {
  const { t } = useLang();
  return (
    <Button variant="ghost" disabled={busy} onClick={onCancel}>
      {t("common.cancel")}
    </Button>
  );
}
