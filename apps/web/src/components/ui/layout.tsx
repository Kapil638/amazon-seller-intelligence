import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export function PageHeader({
  title,
  description,
  children,
  align = "start",
}: {
  title: string;
  description?: string;
  children?: ReactNode;
  align?: "start" | "center";
}) {
  return (
    <header
      className={cn(
        "flex flex-col gap-4",
        align === "center" ? "items-center text-center" : "mb-8 lg:flex-row lg:items-end lg:justify-between",
      )}
    >
      <div className="max-w-2xl space-y-2">
        <h1 className="text-[2rem] font-semibold leading-tight tracking-tight text-foreground">
          {title}
        </h1>
        {description ? (
          <p className="text-[0.9375rem] leading-relaxed text-muted-foreground">{description}</p>
        ) : null}
      </div>
      {children}
    </header>
  );
}

export function Section({
  title,
  description,
  eyebrow,
  action,
  children,
  className,
}: {
  title: string;
  description?: string;
  eyebrow?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("space-y-4", className)}>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="space-y-1">
          {eyebrow ? (
            <p className="text-xs font-medium text-muted-foreground">{eyebrow}</p>
          ) : null}
          <h2 className="text-xl font-semibold tracking-tight">{title}</h2>
          {description ? (
            <p className="max-w-2xl text-sm leading-relaxed text-muted-foreground">{description}</p>
          ) : null}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

export function Panel({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "rounded-lg border border-border bg-surface text-card-foreground shadow-[var(--shadow-sm)]",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function Kpi({
  label,
  value,
  hint,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
}) {
  return (
    <div className="min-w-0">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-xl font-semibold tabular-nums tracking-tight">{value}</p>
      {hint ? <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

export function EmptyState({ title, description }: { title: string; description: string }) {
  return (
    <div className="rounded-lg border border-dashed border-border px-6 py-10 text-center">
      <p className="text-sm font-medium">{title}</p>
      <p className="mt-1 text-sm text-muted-foreground">{description}</p>
    </div>
  );
}
