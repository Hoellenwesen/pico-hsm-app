import type { HTMLAttributes } from "react";
import { cn } from "../../lib/utils";

type Variant = "default" | "success" | "warning" | "destructive" | "info" | "outline" | "muted";

const styles: Record<Variant, string> = {
  default: "bg-muted text-foreground border-border",
  success: "bg-emerald-500/15 text-emerald-500 border-emerald-500/30",
  warning: "bg-amber-500/15 text-amber-500 border-amber-500/30",
  destructive: "bg-red-500/15 text-red-500 border-red-500/30",
  info: "bg-sky-500/15 text-sky-500 border-sky-500/30",
  outline: "bg-transparent text-muted-foreground border-border",
  muted: "bg-muted text-muted-foreground border-transparent",
};

export function Badge({
  variant = "default",
  className,
  ...props
}: HTMLAttributes<HTMLSpanElement> & { variant?: Variant }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-medium",
        styles[variant],
        className,
      )}
      {...props}
    />
  );
}
