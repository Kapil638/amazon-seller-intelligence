import { Check, Circle, Loader2, ShieldAlert, X } from "lucide-react";

import type { ActivityItem } from "@/lib/copilot-view";
import { cn } from "@/lib/utils";

export function CopilotActivityTimeline({
  items,
  emptyLabel = "Ask a question to see what Copilot does.",
}: {
  items: ActivityItem[];
  emptyLabel?: string;
}) {
  if (!items.length) {
    return <p className="text-sm text-muted-foreground">{emptyLabel}</p>;
  }
  return (
    <ol className="space-y-2.5" aria-label="Copilot activity">
      {items.map((item) => (
        <li key={item.id} className="flex items-start gap-2.5 text-sm">
          <StatusIcon status={item.status} />
          <span
            className={cn(
              "leading-5",
              item.status === "failed" ? "text-destructive" : "text-foreground",
            )}
          >
            {item.label}
          </span>
        </li>
      ))}
    </ol>
  );
}

function StatusIcon({ status }: { status: ActivityItem["status"] }) {
  if (status === "done") {
    return <Check className="mt-0.5 h-4 w-4 shrink-0 text-positive" aria-hidden />;
  }
  if (status === "active") {
    return <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-primary" aria-hidden />;
  }
  if (status === "blocked") {
    return <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-warning" aria-hidden />;
  }
  if (status === "failed") {
    return <X className="mt-0.5 h-4 w-4 shrink-0 text-destructive" aria-hidden />;
  }
  return <Circle className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />;
}
