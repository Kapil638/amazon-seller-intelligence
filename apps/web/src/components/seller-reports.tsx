"use client";

import { useMemo, useState, type ChangeEvent, type DragEvent } from "react";
import { Loader2 } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { analyzeReport, ReportAnalysisError } from "@/lib/api";
import { cn } from "@/lib/utils";
import type {
  BusinessReportAnalysis,
  CampaignSummary,
  ProductPerformanceRow,
  ReportFinding,
  ReportFindingSeverity,
  SearchTermReportAnalysis,
  SearchTermSummary,
  WastedSpendRow,
} from "@/lib/types";

const MAX_BYTES = 26_214_400;

const SEVERITY_STYLES: Record<ReportFindingSeverity, string> = {
  high: "border-transparent bg-destructive text-destructive-foreground",
  medium: "border-transparent bg-amber-100 text-amber-900",
  low: "border-transparent bg-secondary text-secondary-foreground",
  info: "border-border bg-background text-muted-foreground",
};

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

function Kpi({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border bg-background p-4">
      <p className="text-xs uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="mt-1 text-lg font-semibold tabular-nums">{value}</p>
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

  return (
    <div className="mx-auto w-full max-w-6xl space-y-8">
      <header className="space-y-3">
        <p className="text-xs font-medium uppercase tracking-[0.22em] text-muted-foreground">
          Milestone 9
        </p>
        <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">Seller Reports</h1>
        <p className="max-w-2xl text-base text-muted-foreground">
          Upload an Amazon Seller Central export. Analytics are deterministic and stay in memory.
          Nothing is saved.
        </p>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>Upload Amazon Seller Central report</CardTitle>
          <CardDescription>
            Supported: Sponsored Products Search Term Report and Business Report (.csv or .xlsx).
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <label
            onDragOver={(event) => {
              event.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDrop}
            className={cn(
              "flex cursor-pointer flex-col items-center justify-center rounded-xl border border-dashed px-6 py-10 text-center",
              dragOver ? "border-primary bg-accent" : "border-border bg-muted/30",
            )}
          >
            <input
              type="file"
              accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              className="sr-only"
              onChange={onInputChange}
            />
            <p className="text-sm font-medium">Drag & drop CSV/XLSX</p>
            <p className="mt-1 text-sm text-muted-foreground">or choose a file</p>
          </label>

          {file ? (
            <p className="text-sm text-muted-foreground">
              {file.name} · {formatBytes(file.size)}
            </p>
          ) : null}

          <Button type="button" size="lg" disabled={!file || loading} onClick={() => void onAnalyze()}>
            {loading ? (
              <>
                <Loader2 className="animate-spin" />
                Analyzing report...
              </>
            ) : (
              "Analyze Report"
            )}
          </Button>
        </CardContent>
      </Card>

      {error ? (
        <Alert variant="destructive">
          <AlertTitle>Report could not be analyzed</AlertTitle>
          <AlertDescription className="whitespace-pre-line">{error}</AlertDescription>
        </Alert>
      ) : null}

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
    <ul className="space-y-3">
      {findings.map((finding) => (
        <li key={`${finding.code}-${finding.entity ?? ""}-${finding.message}`} className="flex gap-3">
          <Badge className={cn("mt-0.5 uppercase", SEVERITY_STYLES[finding.severity])}>
            {finding.severity}
          </Badge>
          <p className="text-sm leading-6">{finding.message}</p>
        </li>
      ))}
    </ul>
  );
}

function SearchTermView({ result }: { result: SearchTermReportAnalysis }) {
  const summary = result.summary;
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>PPC Summary</CardTitle>
          <CardDescription>
            Search used {result.meta.valid_rows} valid rows
            {result.meta.invalid_rows ? ` · ${result.meta.invalid_rows} skipped` : ""}. Metrics are
            observed from this file only.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-3 lg:grid-cols-5">
          <Kpi label="Spend" value={formatInr(summary.spend)} />
          <Kpi label="Sales" value={formatInr(summary.sales)} />
          <Kpi label="Orders" value={formatNumber(summary.orders)} />
          <Kpi label="ACOS" value={formatPercent(summary.acos)} />
          <Kpi label="ROAS" value={formatRoas(summary.roas)} />
          <Kpi label="Clicks" value={formatNumber(summary.clicks)} />
          <Kpi label="CPC" value={formatInr(summary.cpc)} />
          <Kpi label="CTR" value={formatPercent(summary.ctr)} />
          <Kpi label="CVR" value={formatPercent(summary.cvr)} />
        </CardContent>
      </Card>

      {result.warnings.length ? (
        <Alert>
          <AlertTitle>Row warnings</AlertTitle>
          <AlertDescription>
            {result.warnings.length} row{result.warnings.length === 1 ? "" : "s"} could not be read
            and were skipped.
          </AlertDescription>
        </Alert>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>Wasted Spend</CardTitle>
          <CardDescription>
            Heuristic: zero-order spend at or above ₹500, or high observed ACOS (≥ 50%) with
            meaningful spend. This is not a profitability verdict.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <WastedTable rows={result.tables.wasted_spend} />
        </CardContent>
      </Card>

      {result.tables.negative_keyword_candidates.length ? (
        <Card>
          <CardHeader>
            <CardTitle>Negative-keyword candidates</CardTitle>
            <CardDescription>
              Review these search terms. Nothing is applied automatically.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            {result.tables.negative_keyword_candidates.map((item) => (
              <p key={item.search_term}>
                <span className="font-medium">{item.search_term}</span>
                {" · "}
                {formatInr(item.spend)} spend · {item.message}
              </p>
            ))}
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>Search Term Performance</CardTitle>
        </CardHeader>
        <CardContent>
          <SearchTermTable rows={result.tables.search_terms} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Campaign Performance</CardTitle>
          <CardDescription>Budget efficiency is not inferred. Spend and sales are observed totals.</CardDescription>
        </CardHeader>
        <CardContent>
          <CampaignTable rows={result.tables.campaigns} />
        </CardContent>
      </Card>

      {result.tables.strong_search_terms.length ? (
        <Card>
          <CardHeader>
            <CardTitle>Strong observed search-term performance</CardTitle>
            <CardDescription>
              Orders with enough clicks and conversion. Not labeled as winning keywords.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-1 text-sm">
            {result.tables.strong_search_terms.map((item) => (
              <p key={item.search_term}>
                {item.search_term} · {item.orders} orders · CVR {formatPercent(item.cvr)}
              </p>
            ))}
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>Findings</CardTitle>
        </CardHeader>
        <CardContent>
          <Findings findings={result.findings} />
        </CardContent>
      </Card>
    </div>
  );
}

function BusinessView({ result }: { result: BusinessReportAnalysis }) {
  const summary = result.summary;
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Business Summary</CardTitle>
          <CardDescription>
            {result.meta.valid_rows} valid rows
            {result.meta.invalid_rows ? ` · ${result.meta.invalid_rows} skipped` : ""}.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <Kpi label="Sessions" value={formatNumber(summary.sessions)} />
          <Kpi label="Page Views" value={formatNumber(summary.page_views)} />
          <Kpi label="Units Ordered" value={formatNumber(summary.units_ordered)} />
          <Kpi label="Sales" value={formatInr(summary.ordered_product_sales)} />
          <Kpi label="Conversion" value={formatPercent(summary.conversion)} />
          <Kpi label="Buy Box %" value={formatPercent(summary.buy_box_percentage)} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Product Performance</CardTitle>
        </CardHeader>
        <CardContent>
          <ProductTable rows={result.tables.products} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Performance Findings</CardTitle>
        </CardHeader>
        <CardContent>
          <Findings findings={result.findings} />
        </CardContent>
      </Card>
    </div>
  );
}

function WastedTable({ rows }: { rows: WastedSpendRow[] }) {
  if (!rows.length) {
    return <p className="text-sm text-muted-foreground">No wasted-spend candidates in this file.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[40rem] text-left text-sm">
        <thead className="text-xs uppercase text-muted-foreground">
          <tr>
            <th className="pb-2 pr-3">Search Term</th>
            <th className="pb-2 pr-3">Spend</th>
            <th className="pb-2 pr-3">Clicks</th>
            <th className="pb-2 pr-3">Orders</th>
            <th className="pb-2 pr-3">Sales</th>
            <th className="pb-2 pr-3">Reason</th>
            <th className="pb-2">Severity</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={`${row.search_term}-${row.reason_code}`} className="border-t border-border">
              <td className="py-2 pr-3">{row.search_term}</td>
              <td className="py-2 pr-3 tabular-nums">{formatInr(row.spend)}</td>
              <td className="py-2 pr-3 tabular-nums">{formatNumber(row.clicks)}</td>
              <td className="py-2 pr-3 tabular-nums">{formatNumber(row.orders)}</td>
              <td className="py-2 pr-3 tabular-nums">{formatInr(row.sales)}</td>
              <td className="py-2 pr-3">{row.reason}</td>
              <td className="py-2">
                <Badge className={cn("uppercase", SEVERITY_STYLES[row.severity])}>{row.severity}</Badge>
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
      <table className="w-full min-w-[52rem] text-left text-sm">
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
      <table className="w-full min-w-[48rem] text-left text-sm">
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
