import type { LucideIcon } from "lucide-react";
import { Badge } from "../components/ui/Badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/Card";

export function DummyPage({ title, subtitle, icon: Icon }: { title: string; subtitle: string; icon: LucideIcon }) {
  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-bold tracking-tight">{title}</h1>
        <p className="text-sm text-muted-foreground">{subtitle}</p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>
            <Icon size={16} className="text-primary" /> {title}
          </CardTitle>
          <CardDescription>Not implemented yet — no device calls here.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-3">
          <Badge variant="warning">Design preview</Badge>
          <Badge variant="outline">Planned slice</Badge>
        </CardContent>
      </Card>
    </div>
  );
}
