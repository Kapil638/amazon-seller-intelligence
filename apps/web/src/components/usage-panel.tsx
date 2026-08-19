"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";

import { fetchUsageDashboard } from "@/lib/api";
import type {
  OpenAIAccountUsage,
  OpenAIAppUsage,
  RainforestAccountUsage,
  RainforestAppUsage,
  UsageDashboardResponse,
  UsageWarningLevel,
} from "@/lib/types";
import { cn } from "@/lib/utils";

const POLL_MS = 60_000;

export function UsagePanel() {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<UsageDashboardResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const panelId = useId();

  const load = useCallback(async (refresh = false) => {
    setRefreshing(true);
    try {
      const next = await fetchUsageDashboard(refresh);
      setData(next);
      setError(null);
    } catch {
      setError("Usage temporarily unavailable");
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load(false);
    const id = window.setInterval(() => {
      void load(false);
    }, POLL_MS);
    return () => window.clearInterval(id);
  }, [load]);

  useEffect(() => {
    if (!open) {
      return;
    }
    function onPointer(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const rainforestLabel = rainforestSummary(data?.rainforest.account);
  const openaiLabel = openaiSummary(data?.openai.account);

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((value) => !value)}
        className={cn(
          "flex max-w-[18rem] items-center gap-3 rounded-md px-2 py-1.5 text-left transition-colors duration-200 hover:bg-surface-subtle",
          open && "bg-surface-subtle",
        )}
      >
        <span className="text-xs font-medium text-muted-foreground">Usage</span>
        <span className="hidden text-xs tabular-nums text-foreground sm:inline">
          RF {rainforestLabel}
        </span>
        <span className="hidden text-xs tabular-nums text-foreground md:inline">
          AI {openaiLabel}
        </span>
      </button>
      {open ? (
        <div
          id={panelId}
          role="dialog"
          aria-label="API usage"
          className="absolute right-0 z-50 mt-2 w-[22.5rem] max-w-[calc(100vw-2rem)] rounded-lg border border-border bg-surface-elevated p-4 shadow-[var(--shadow-sm)]"
        >
          <div className="mb-3 flex items-center justify-between">
            <p className="text-sm font-medium">Usage</p>
            <button
              type="button"
              onClick={() => void load(true)}
              disabled={refreshing}
              className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors duration-200 hover:bg-surface-subtle hover:text-foreground disabled:opacity-50"
              aria-label="Refresh provider account usage"
            >
              <RefreshCw className={cn("h-3.5 w-3.5", refreshing && "animate-spin")} />
            </button>
          </div>
          {error && !data ? (
            <p className="text-sm text-muted-foreground">{error}</p>
          ) : data ? (
            <div className="space-y-5">
              <RainforestBlock account={data.rainforest.account} app={data.rainforest.app} />
              <OpenAIBlock account={data.openai.account} app={data.openai.app} />
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">Loading usage…</p>
          )}
        </div>
      ) : null}
    </div>
  );
}

function RainforestBlock({
  account,
  app,
}: {
  account: RainforestAccountUsage;
  app: RainforestAppUsage;
}) {
  const used = account.credits_used;
  const limit = account.credits_limit;
  const remaining = account.credits_remaining;
  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between">
        <p className="text-sm font-medium">Rainforest</p>
        <p className="text-[11px] text-muted-foreground">Provider account</p>
      </div>
      {account.available && used != null && limit != null ? (
        <>
          <p className="text-sm tabular-nums">
            {used.toLocaleString()} / {limit.toLocaleString()} credits
          </p>
          <UsageBar percent={account.usage_percentage} level={account.warning_level} />
          <p className="text-xs text-muted-foreground">
            {account.usage_percentage != null ? `${formatPercent(account.usage_percentage)} used` : null}
            {remaining != null ? ` · ${remaining.toLocaleString()} remaining` : ""}
            {" · "}
            Resets {formatReset(account.reset_at)}
          </p>
        </>
      ) : (
        <p className="text-xs text-muted-foreground">{account.message || "Unavailable"}</p>
      )}
      <p className="text-xs text-muted-foreground">
        This app: {app.product_calls + app.search_calls} calls · {app.calls_saved} saved by cache
      </p>
    </div>
  );
}

function OpenAIBlock({
  account,
  app,
}: {
  account: OpenAIAccountUsage;
  app: OpenAIAppUsage;
}) {
  return (
    <div className="space-y-2 border-t border-border pt-4">
      <div className="flex items-baseline justify-between">
        <p className="text-sm font-medium">OpenAI</p>
        <p className="text-[11px] text-muted-foreground">Provider spend</p>
      </div>
      {account.available && account.spend_usd != null ? (
        <>
          <p className="text-sm tabular-nums">
            {formatUsd(account.spend_usd)}
            {account.budget_usd != null ? ` / ${formatUsd(account.budget_usd)}` : ""}
          </p>
          <UsageBar percent={account.usage_percentage} level={account.warning_level} />
        </>
      ) : (
        <p className="text-xs text-muted-foreground">{account.message || "Unavailable"}</p>
      )}
      <div className="rounded-md bg-surface-subtle px-3 py-2">
        <p className="text-[11px] font-medium text-muted-foreground">This app</p>
        <p className="mt-1 text-xs text-muted-foreground">
          {formatAppCost(app)} · {formatTokens(app.total_tokens)} tokens · {app.requests}{" "}
          {app.requests === 1 ? "request" : "requests"} · {app.calls_saved} calls saved
        </p>
        <p className="mt-1 text-[11px] text-muted-foreground">
          Estimated from token usage. Not the OpenAI invoice.
        </p>
      </div>
    </div>
  );
}

function UsageBar({ percent, level }: { percent: number | null; level: UsageWarningLevel }) {
  const width = Math.max(0, Math.min(percent ?? 0, 100));
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-subtle">
      <div
        className={cn(
          "h-full rounded-full transition-[width] duration-200",
          level === "critical" && "bg-destructive",
          level === "warning" && "bg-warning",
          (level === "normal" || level === "unknown") && "bg-primary",
        )}
        style={{ width: `${width}%` }}
      />
    </div>
  );
}

function rainforestSummary(account: RainforestAccountUsage | undefined): string {
  if (!account?.available || account.credits_used == null || account.credits_limit == null) {
    return "—";
  }
  return `${account.credits_used}/${account.credits_limit}`;
}

function openaiSummary(account: OpenAIAccountUsage | undefined): string {
  if (!account?.available || account.spend_usd == null) {
    return "—";
  }
  return formatUsd(account.spend_usd);
}

function formatPercent(value: number): string {
  return `${Number.isInteger(value) ? value.toFixed(0) : value.toFixed(1)}%`;
}

function formatReset(value: string | null): string {
  if (!value) {
    return "unknown";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "unknown";
  }
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short" }).format(date);
}

function formatUsd(value: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

function formatTokens(value: number): string {
  if (value < 1000) {
    return value.toLocaleString();
  }
  if (value < 1_000_000) {
    return `${(value / 1000).toFixed(1)}K`;
  }
  return `${(value / 1_000_000).toFixed(1)}M`;
}

function formatAppCost(app: OpenAIAppUsage): string {
  if (app.cost_status === "unavailable" || app.estimated_spend_usd == null) {
    return "cost unavailable";
  }
  const label = `${formatUsd(app.estimated_spend_usd)} estimated`;
  return app.cost_status === "partial" ? `${label} (partial)` : label;
}
