import { cn } from "@/lib/utils";

export function ScoreBar({
  label,
  score,
  max = 100,
}: {
  label: string;
  score: number;
  max?: number;
}) {
  const width = Math.max(0, Math.min((score / max) * 100, 100));
  return (
    <div className="grid grid-cols-[7.5rem_1fr_2.75rem] items-center gap-3">
      <span className="truncate text-sm text-muted-foreground">{label}</span>
      <div className="h-1.5 overflow-hidden rounded-full bg-surface-subtle">
        <div
          className="h-full rounded-full bg-primary transition-[width] duration-200"
          style={{ width: `${width}%` }}
        />
      </div>
      <span className="text-right text-sm font-medium tabular-nums">{score}</span>
    </div>
  );
}

export function SeverityDot({
  severity,
}: {
  severity: "high" | "medium" | "low" | "info";
}) {
  return (
    <span
      className={cn(
        "mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full",
        severity === "high" && "bg-destructive",
        severity === "medium" && "bg-warning",
        severity === "low" && "bg-muted-foreground/50",
        severity === "info" && "bg-info",
      )}
      aria-hidden
    />
  );
}

export function SeverityLabel({
  severity,
}: {
  severity: "high" | "medium" | "low" | "info";
}) {
  const label =
    severity === "high"
      ? "High"
      : severity === "medium"
        ? "Medium"
        : severity === "low"
          ? "Low"
          : "Info";
  return (
    <span
      className={cn(
        "text-[11px] font-medium",
        severity === "high" && "text-destructive",
        severity === "medium" && "text-warning",
        severity === "low" && "text-muted-foreground",
        severity === "info" && "text-info",
      )}
    >
      {label}
    </span>
  );
}
