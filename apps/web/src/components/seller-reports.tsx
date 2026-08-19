"use client";

import { useMemo, useState, type ChangeEvent, type DragEvent } from "react";
import { Loader2 } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Kpi, PageHeader, Panel } from "@/components/ui/layout";
import { SeverityDot, SeverityLabel } from "@/components/ui/score";
import { analyzeReport, ReportAnalysisError } from "@/lib/api";
import { cn } from "@/lib/utils";
import type {
  BusinessReportAnalysis,
  CampaignSummary,
  ProductPerformanceRow,
  ReportFinding,
  SearchTermReportAnalysis,
  SearchTermSummary,
  WastedSpendRow,
} from "@/lib/types";

const MAX_BYTES = 26_214_400;

function formatInr(value: string | null | undefined): string {
  if (value == null || value === "") {
    return "Not available";
  }
  const amount = Number(value);
  if (Number.isNaN(amount)) {
    return "Not available";
  }
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 2,
  }).format(amount);
}

function formatPercent(value: string | null | undefined): string {
  if (value == null || value === "") {
    return "Not available";
  }
  const amount = Number(value);
  if (Number.isNaN(amount)) {
    return "Not available";
  }
  return `${(amount * 100).toFixed(1)}%`;
}

function formatRoas(value: string | null | undefined): string {
  if (value == null || value === "") {
    return "Not available";
  }
  const amount = Number(value);
  if (Number.isNaN(amount)) {
    return "Not available";
  }
  return `${amount.toFixed(2)}x`;
}

function formatNumber(value: number | null | undefined): string {
  if (value == null) {
    return "Not available";
  }
  return new Intl.NumberFormat("en-IN").format(value);
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function KpiGrid({ items }: { items: Array<{ label: string; value: string }> }) {
  return (
    <div className="grid gap-6 sm:grid-cols-3 lg:grid-cols-5">
      {items.map((item) => (
        <Kpi key={item.label} label={item.label} value={item.value} />
      ))}
    </div>
  );
}

export function SellerReports() {
  const [file, setFile] = useState<File | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<SearchTermReportAnalysis | BusinessReportAnalysis | null>(
    null,
  );

  function acceptFile(next: File | null) {
    setResult(null);
    setError(null);
    if (!next) {
      setFile(null);
      return;
    }
    const name = next.name.toLowerCase();
    if (!name.endsWith(".csv") && !name.endsWith(".xlsx")) {
      setFile(null);
      setError("Upload a .csv or .xlsx file.");
      return;
    }
    if (next.size > MAX_BYTES) {
      setFile(null);
      setError("This file is larger than the 25 MB upload limit.");
      return;
    }
    if (next.size === 0) {
      setFile(null);
      setError("This file is empty.");
      return;
    }
    setFile(next);
  }

  function onInputChange(event: ChangeEvent<HTMLInputElement>) {
    acceptFile(event.target.files?.[0] ?? null);
    event.target.value = "";
  }

  function onDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setDragOver(false);
    acceptFile(event.dataTransfer.files?.[0] ?? null);
  }

  async function onAnalyze() {
    if (!file || loading) {
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const analysis = await analyzeReport(file);
      setResult(analysis);
    } catch (caught) {
      setResult(null);
      setError(
        caught instanceof ReportAnalysisError
          ? caught.message
          : "Something went wrong while analyzing this report.",
      );
    } finally {
      setLoading(false);
    }
  }

  const idle = !result;

  return (
    <div className={idle ? "flex min-h-[calc(100dvh-7.5rem)] flex-col items-center justify-center" : "space-y-8"}>
      <div className={idle ? "w-full max-w-2xl space-y-6" : "space-y-8"}>
      <PageHeader
        align={idle ? "center" : "start"}
        title="Seller reports"
        description="Upload an Amazon Seller Central export. Analytics are deterministic and stay in this session. Nothing is saved."
      />

      <Panel className="p-5">
        <div className={cn("mb-4 space-y-1", idle && "text-center")}>
          <h2 className="text-[0.95rem] font-semibold">Upload report</h2>
          <p className="text-sm text-muted-foreground">
            Sponsored Products Search Term Report or Business Report (.csv or .xlsx).
          </p>
        </div>
        <div className="space-y-4">
          <label
            onDragOver={(event) => {
              event.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDrop}
            className={cn(
              "flex cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed px-6 py-10 text-center transition-colors duration-200",
              dragOver ? "border-primary bg-surface-subtle" : "border-border bg-surface-subtle/50",
            )}
          >
            <input
              type="file"
              accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              className="sr-only"
              onChange={onInputChange}
            />
            <p className="text-sm font-medium">Drop a CSV or XLSX file</p>
            <p className="mt-1 text-sm text-muted-foreground">or click to choose a file, up to 25 MB</p>
          </label>

          {file ? (
            <p className={cn("text-sm text-muted-foreground", idle && "text-center")}>
              {file.name} · {formatBytes(file.size)}
            </p>
          ) : null}

          <div className={idle ? "flex justify-center" : undefined}>
            <Button type="button" disabled={!file || loading} onClick={() => void onAnalyze()}>
              {loading ? (
                <>
                  <Loader2 className="animate-spin" />
                  Analyzing report…
                </>
              ) : (
                "Analyze report"
              )}
            </Button>
          </div>
        </div>
      </Panel>

      {error ? (
        <Alert variant="destructive">
          <AlertTitle>Report could not be analyzed</AlertTitle>
          <AlertDescription className="whitespace-pre-line">{error}</AlertDescription>
        </Alert>
      ) : null}
      </div>

      {result?.report_type === "search_term_report" ? (
        <SearchTermView result={result} />
      ) : null}
      {result?.report_type === "business_report" ? <BusinessView result={result} /> : null}
    </div>
  );
}

function Findings({ findings }: { findings: ReportFinding[] }) {
  if (!findings.length) {
    return <p className="text-sm text-muted-foreground">No findings for this report.</p>;
  }
  return (
    <ul className="divide-y divide-border">
      {findings.map((finding) => (
        <li key={`${finding.code}-${finding.entity ?? ""}-${finding.message}`} className="flex gap-3 py-3 first:pt-0 last:pb-0">
          <SeverityDot severity={finding.severity} />
          <div className="min-w-0 space-y-1">
            <SeverityLabel severity={finding.severity} />
            <p className="text-sm leading-6">{finding.message}</p>
          </div>
        </li>
      ))}
    </ul>
  );
}

function SearchTermView({ result }: { result: SearchTermReportAnalysis }) {
  const summary = result.summary;
  return (
    <div className="space-y-6">
      <Panel className="p-5">
        <div className="mb-5 space-y-1">
          <h2 className="text-[0.95rem] font-semibold">PPC summary</h2>
          <p className="text-sm text-muted-foreground">
            {result.meta.valid_rows} valid rows
            {result.meta.invalid_rows ? ` · ${result.meta.invalid_rows} skipped` : ""}. Metrics are
            observed from this file only.
          </p>
        </div>
        <KpiGrid
          items={[
            { label: "Spend", value: formatInr(summary.spend) },
            { label: "Sales", value: formatInr(summary.sales) },
            { label: "Orders", value: formatNumber(summary.orders) },
            { label: "ACOS", value: formatPercent(summary.acos) },
            { label: "ROAS", value: formatRoas(summary.roas) },
            { label: "Clicks", value: formatNumber(summary.clicks) },
            { label: "CPC", value: formatInr(summary.cpc) },
            { label: "CTR", value: formatPercent(summary.ctr) },
            { label: "CVR", value: formatPercent(summary.cvr) },
          ]}
        />
      </Panel>

      {result.warnings.length ? (
        <Alert>
          <AlertTitle>Row warnings</AlertTitle>
          <AlertDescription>
            {result.warnings.length} row{result.warnings.length === 1 ? "" : "s"} could not be read
            and were skipped.
          </AlertDescription>
        </Alert>
      ) : null}

      <Panel className="p-5">
        <div className="mb-4 space-y-1">
          <h2 className="text-[0.95rem] font-semibold">Wasted spend</h2>
          <p className="text-sm text-muted-foreground">
            Heuristic: zero-order spend at or above ₹500, or high observed ACOS (≥ 50%) with
            meaningful spend. This is not a profitability verdict.
          </p>
        </div>
        <WastedTable rows={result.tables.wasted_spend} />
      </Panel>

      {result.tables.negative_keyword_candidates.length ? (
        <Panel className="p-5">
          <div className="mb-4 space-y-1">
            <h2 className="text-[0.95rem] font-semibold">Negative-keyword candidates</h2>
            <p className="text-sm text-muted-foreground">Review these search terms. Nothing is applied automatically.</p>
          </div>
          <div className="space-y-2 text-sm">
            {result.tables.negative_keyword_candidates.map((item) => (
              <p key={item.search_term}>
                <span className="font-medium">{item.search_term}</span>
                {" · "}
                {formatInr(item.spend)} spend · {item.message}
              </p>
            ))}
          </div>
        </Panel>
      ) : null}

      <Panel className="overflow-hidden p-5">
        <h2 className="mb-4 text-[0.95rem] font-semibold">Search term performance</h2>
        <SearchTermTable rows={result.tables.search_terms} />
      </Panel>

      <Panel className="overflow-hidden p-5">
        <div className="mb-4 space-y-1">
          <h2 className="text-[0.95rem] font-semibold">Campaign performance</h2>
          <p className="text-sm text-muted-foreground">
            Budget efficiency is not inferred. Spend and sales are observed totals.
          </p>
        </div>
        <CampaignTable rows={result.tables.campaigns} />
      </Panel>

      {result.tables.strong_search_terms.length ? (
        <Panel className="p-5">
          <div className="mb-4 space-y-1">
            <h2 className="text-[0.95rem] font-semibold">Strong observed search-term performance</h2>
            <p className="text-sm text-muted-foreground">
              Orders with enough clicks and conversion. Not labeled as winning keywords.
            </p>
          </div>
          <div className="space-y-1 text-sm">
            {result.tables.strong_search_terms.map((item) => (
              <p key={item.search_term}>
                {item.search_term} · {item.orders} orders · CVR {formatPercent(item.cvr)}
              </p>
            ))}
          </div>
        </Panel>
      ) : null}

      <Panel className="p-5">
        <h2 className="mb-4 text-[0.95rem] font-semibold">Findings</h2>
        <Findings findings={result.findings} />
      </Panel>
    </div>
  );
}

function BusinessView({ result }: { result: BusinessReportAnalysis }) {
  const summary = result.summary;
  return (
    <div className="space-y-6">
      <Panel className="p-5">
        <div className="mb-5 space-y-1">
          <h2 className="text-[0.95rem] font-semibold">Business summary</h2>
          <p className="text-sm text-muted-foreground">
            {result.meta.valid_rows} valid rows
            {result.meta.invalid_rows ? ` · ${result.meta.invalid_rows} skipped` : ""}.
          </p>
        </div>
        <KpiGrid
          items={[
            { label: "Sessions", value: formatNumber(summary.sessions) },
            { label: "Page views", value: formatNumber(summary.page_views) },
            { label: "Units ordered", value: formatNumber(summary.units_ordered) },
            { label: "Sales", value: formatInr(summary.ordered_product_sales) },
            { label: "Conversion", value: formatPercent(summary.conversion) },
            { label: "Buy Box %", value: formatPercent(summary.buy_box_percentage) },
          ]}
        />
      </Panel>

      <Panel className="overflow-hidden p-5">
        <h2 className="mb-4 text-[0.95rem] font-semibold">Product performance</h2>
        <ProductTable rows={result.tables.products} />
      </Panel>

      <Panel className="p-5">
        <h2 className="mb-4 text-[0.95rem] font-semibold">Performance findings</h2>
        <Findings findings={result.findings} />
      </Panel>
    </div>
  );
}

function WastedTable({ rows }: { rows: WastedSpendRow[] }) {
  if (!rows.length) {
    return <p className="text-sm text-muted-foreground">No wasted-spend candidates in this file.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="data-table min-w-[40rem]">
        <thead>
          <tr>
            <th>Search term</th>
            <th className="num">Spend</th>
            <th className="num">Clicks</th>
            <th className="num">Orders</th>
            <th className="num">Sales</th>
            <th>Reason</th>
            <th>Severity</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={`${row.search_term}-${row.reason_code}`}>
              <td>{row.search_term}</td>
              <td className="num">{formatInr(row.spend)}</td>
              <td className="num">{formatNumber(row.clicks)}</td>
              <td className="num">{formatNumber(row.orders)}</td>
              <td className="num">{formatInr(row.sales)}</td>
              <td>{row.reason}</td>
              <td>
                <SeverityLabel severity={row.severity} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SearchTermTable({ rows }: { rows: SearchTermSummary[] }) {
  return <SortablePpcTable rows={rows} nameKey="search_term" nameLabel="Search Term" />;
}

function CampaignTable({ rows }: { rows: CampaignSummary[] }) {
  return <SortablePpcTable rows={rows} nameKey="campaign_name" nameLabel="Campaign" />;
}

function SortablePpcTable<T extends SearchTermSummary | CampaignSummary>({
  rows,
  nameKey,
  nameLabel,
}: {
  rows: T[];
  nameKey: "search_term" | "campaign_name";
  nameLabel: string;
}) {
  const [sortKey, setSortKey] = useState<string>("spend");
  const [dir, setDir] = useState<"asc" | "desc">("desc");
  const sorted = useMemo(() => {
    const copy = [...rows];
    copy.sort((left, right) => {
      const a = Number((left as Record<string, unknown>)[sortKey] ?? 0);
      const b = Number((right as Record<string, unknown>)[sortKey] ?? 0);
      if (Number.isNaN(a) || Number.isNaN(b)) {
        const as = String((left as Record<string, unknown>)[sortKey] ?? "");
        const bs = String((right as Record<string, unknown>)[sortKey] ?? "");
        return dir === "asc" ? as.localeCompare(bs) : bs.localeCompare(as);
      }
      return dir === "asc" ? a - b : b - a;
    });
    return copy;
  }, [dir, rows, sortKey]);

  function toggle(key: string) {
    if (sortKey === key) {
      setDir((value) => (value === "asc" ? "desc" : "asc"));
      return;
    }
    setSortKey(key);
    setDir("desc");
  }

  if (!rows.length) {
    return <p className="text-sm text-muted-foreground">No rows to display.</p>;
  }

  const headers = [
    [nameKey, nameLabel],
    ["spend", "Spend"],
    ["sales", "Sales"],
    ["orders", "Orders"],
    ["acos", "ACOS"],
    ["roas", "ROAS"],
    ["clicks", "Clicks"],
    ["cpc", "CPC"],
    ["ctr", "CTR"],
    ["cvr", "CVR"],
  ] as const;

  return (
    <div className="overflow-x-auto">
      <table className="data-table min-w-[52rem]">
        <thead className="text-xs uppercase text-muted-foreground">
          <tr>
            {headers.map(([key, label]) => (
              <th key={key} className="pb-2 pr-3">
                <button type="button" className="font-medium hover:text-foreground" onClick={() => toggle(key)}>
                  {label}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => {
            const label = nameKey === "search_term"
              ? (row as SearchTermSummary).search_term
              : (row as CampaignSummary).campaign_name;
            return (
            <tr key={label} className="border-t border-border">
              <td className="py-2 pr-3">{label}</td>
              <td className="py-2 pr-3 tabular-nums">{formatInr(row.spend)}</td>
              <td className="py-2 pr-3 tabular-nums">{formatInr(row.sales)}</td>
              <td className="py-2 pr-3 tabular-nums">{formatNumber(row.orders)}</td>
              <td className="py-2 pr-3 tabular-nums">{formatPercent(row.acos)}</td>
              <td className="py-2 pr-3 tabular-nums">{formatRoas(row.roas)}</td>
              <td className="py-2 pr-3 tabular-nums">{formatNumber(row.clicks)}</td>
              <td className="py-2 pr-3 tabular-nums">{formatInr(row.cpc)}</td>
              <td className="py-2 pr-3 tabular-nums">{formatPercent(row.ctr)}</td>
              <td className="py-2 pr-3 tabular-nums">{formatPercent(row.cvr)}</td>
            </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ProductTable({ rows }: { rows: ProductPerformanceRow[] }) {
  const [sortKey, setSortKey] = useState<string>("sessions");
  const [dir, setDir] = useState<"asc" | "desc">("desc");
  const sorted = useMemo(() => {
    const copy = [...rows];
    copy.sort((left, right) => {
      const a = Number((left as Record<string, unknown>)[sortKey] ?? 0);
      const b = Number((right as Record<string, unknown>)[sortKey] ?? 0);
      return dir === "asc" ? a - b : b - a;
    });
    return copy;
  }, [dir, rows, sortKey]);

  if (!rows.length) {
    return <p className="text-sm text-muted-foreground">No products to display.</p>;
  }

  function toggle(key: string) {
    if (sortKey === key) {
      setDir((value) => (value === "asc" ? "desc" : "asc"));
      return;
    }
    setSortKey(key);
    setDir("desc");
  }

  return (
    <div className="overflow-x-auto">
      <table className="data-table min-w-[48rem]">
        <thead className="text-xs uppercase text-muted-foreground">
          <tr>
            {[
              ["asin", "ASIN"],
              ["title", "Title"],
              ["sessions", "Sessions"],
              ["units_ordered", "Units"],
              ["ordered_product_sales", "Sales"],
              ["conversion", "Conversion"],
              ["buy_box_percentage", "Buy Box %"],
            ].map(([key, label]) => (
              <th key={key} className="pb-2 pr-3">
                <button type="button" className="font-medium hover:text-foreground" onClick={() => toggle(key)}>
                  {label}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => (
            <tr key={row.asin} className="border-t border-border">
              <td className="py-2 pr-3 font-mono text-xs">{row.asin}</td>
              <td className="py-2 pr-3">{row.title ?? "Not available"}</td>
              <td className="py-2 pr-3 tabular-nums">{formatNumber(row.sessions)}</td>
              <td className="py-2 pr-3 tabular-nums">{formatNumber(row.units_ordered)}</td>
              <td className="py-2 pr-3 tabular-nums">{formatInr(row.ordered_product_sales)}</td>
              <td className="py-2 pr-3 tabular-nums">{formatPercent(row.conversion)}</td>
              <td className="py-2 tabular-nums">{formatPercent(row.buy_box_percentage)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
