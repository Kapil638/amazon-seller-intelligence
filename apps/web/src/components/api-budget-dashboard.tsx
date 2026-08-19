"use client";

import { useCallback, useEffect, useState } from "react";
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

export function ApiBudgetDashboard() {
  const [data, setData] = useState<UsageDashboardResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

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

  return (
    <div className="border-t border-border/80 bg-card/40">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-2 px-4 py-2.5 sm:px-6">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
              API Budget
            </p>
            <p className="text-[11px] text-muted-foreground">
              Provider-account usage and this app’s cache savings are tracked separately.
            </p>
          </div>
          <button
            type="button"
            onClick={() => void load(true)}
            disabled={refreshing}
            className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-50"
            aria-label="Refresh provider account usage"
            title="Refresh provider account usage"
          >
            <RefreshCw className={cn("h-3.5 w-3.5", refreshing && "animate-spin")} />
          </button>
        </div>
        {error && !data ? (
          <p className="text-xs text-muted-foreground">{error}</p>
        ) : data ? (
          <div className="grid gap-2 sm:grid-cols-2">
            <RainforestCard account={data.rainforest.account} app={data.rainforest.app} />
            <OpenAICard account={data.openai.account} app={data.openai.app} />
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">Loading usage…</p>
        )}
      </div>
    </div>
  );
}

function RainforestCard({
  account,
  app,
}: {
  account: RainforestAccountUsage;
  app: RainforestAppUsage;
}) {
  const used = account.credits_used;
  const limit = account.credits_limit;
  const remaining = account.credits_remaining;
  const pct = account.usage_percentage;
  return (
    <article className="rounded-lg border border-border/80 bg-background/70 px-3 py-2">
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold">Rainforest</h2>
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground">Account</span>
      </div>
      {account.available && used != null && limit != null ? (
        <>
          <p className="text-sm tabular-nums">
            {used.toLocaleString()} / {limit.toLocaleString()} credits used
          </p>
          <UsageBar percent={pct} level={account.warning_level} />
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground">
            {pct != null ? <span>{formatPercent(pct)} used</span> : null}
            {remaining != null ? <span>{remaining.toLocaleString()} remaining</span> : null}
            <span>Reset: {formatReset(account.reset_at)}</span>
          </div>
          {account.usage_history.length > 0 ? (
            <details className="mt-1">
              <summary className="cursor-pointer text-[11px] text-muted-foreground">
                Recent credit use
              </summary>
              <ul className="mt-1 space-y-0.5 text-[11px] text-muted-foreground">
                {account.usage_history.map((point) => (
                  <li key={point.date} className="flex justify-between gap-3 tabular-nums">
                    <span>{point.date}</span>
                    <span>{point.credits_used} credits</span>
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </>
      ) : (
        <p className="text-xs text-muted-foreground">
          {account.message || "Usage temporarily unavailable"}
        </p>
      )}
      <div className="mt-2 border-t border-border/70 pt-1.5">
        <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
          This app
        </p>
        <p className="text-[11px] text-muted-foreground">
          {app.product_calls + app.search_calls} calls
          {app.product_calls || app.search_calls
            ? ` (${app.product_calls} product, ${app.search_calls} search)`
            : ""}
          {" · "}
          {app.calls_saved} saved by cache
        </p>
      </div>
    </article>
  );
}

function OpenAICard({
  account,
  app,
}: {
  account: OpenAIAccountUsage;
  app: OpenAIAppUsage;
}) {
  return (
    <article className="rounded-lg border border-border/80 bg-background/70 px-3 py-2">
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold">OpenAI</h2>
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
          {account.available ? "Provider spend" : "Provider"}
        </span>
      </div>
      {account.available && account.spend_usd != null ? (
        <>
          <p className="text-sm tabular-nums">{formatUsd(account.spend_usd)} spent</p>
          {account.budget_usd != null ? (
            <p className="text-[11px] text-muted-foreground">
              Budget: {formatUsd(account.budget_usd)}
            </p>
          ) : null}
          <UsageBar percent={account.usage_percentage} level={account.warning_level} />
        </>
      ) : (
        <p className="text-xs text-muted-foreground">
          {account.message || "Usage temporarily unavailable"}
        </p>
      )}
      <div className="mt-2 border-t border-border/70 pt-1.5">
        <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
          This app
        </p>
        <p className="text-[11px] text-muted-foreground">
          {formatAppCost(app)}
          {" · "}
          {formatTokens(app.total_tokens)} tokens
          {" · "}
          {app.requests} {app.requests === 1 ? "request" : "requests"}
          {" · "}
          {app.calls_saved} calls saved
        </p>
      </div>
    </article>
  );
}

function UsageBar({
  percent,
  level,
}: {
  percent: number | null;
  level: UsageWarningLevel;
}) {
  const width = Math.max(0, Math.min(percent ?? 0, 100));
  return (
    <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-muted">
      <div
        className={cn(
          "h-full rounded-full transition-[width]",
          level === "critical" && "bg-destructive",
          level === "warning" && "bg-amber-600",
          (level === "normal" || level === "unknown") && "bg-primary",
        )}
        style={{ width: `${width}%` }}
      />
    </div>
  );
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
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
  }).format(date);
}

function formatUsd(value: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: value >= 1 ? 2 : 2,
    maximumFractionDigits: 2,
  }).format(value);
}

function formatTokens(value: number): string {
  if (value < 1000) {
    return value.toLocaleString();
  }
  if (value < 10_000) {
    return `${(value / 1000).toFixed(1)}K`;
  }
  if (value < 1_000_000) {
    return `${Math.round(value / 100) / 10}K`;
  }
  return `${(value / 1_000_000).toFixed(1)}M`;
}

function formatAppCost(app: OpenAIAppUsage): string {
  if (app.cost_status === "unavailable" || app.estimated_spend_usd == null) {
    return "cost unavailable";
  }
  const label = formatUsd(app.estimated_spend_usd) + " estimated";
  return app.cost_status === "partial" ? `${label} (partial)` : label;
}
