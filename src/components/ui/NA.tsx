import { Badge } from "./Badge";

/** Muted "not available" badge for values with no device source (yet). */
export function NA() {
  return <Badge variant="muted">N/A</Badge>;
}
