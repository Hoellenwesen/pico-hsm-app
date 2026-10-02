import type { LucideIcon } from "lucide-react";
import { cn } from "../lib/utils";

export function InfoRow({
  icon: Icon,
  label,
  children,
}: {
  icon: LucideIcon;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-lg px-3 py-2 hover:bg-muted/60">
      <span className="flex min-w-0 items-center gap-2.5 text-sm text-muted-foreground">
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-muted text-foreground">
          <Icon size={15} />
        </span>
        <span className="truncate">{label}</span>
      </span>
      <span className={cn("flex shrink-0 items-center gap-2 text-sm font-medium")}>{children}</span>
    </div>
  );
}
