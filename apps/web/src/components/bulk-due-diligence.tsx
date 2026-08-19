"use client";

import { useEffect, useMemo, useState, type ChangeEvent, type DragEvent } from "react";
import { Loader2 } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  downloadBulkReport,
  fetchBulkJob,
  previewBulkFile,
  ReportAnalysisError,
  startBulkJob,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import type {
  BulkAISelection,
  BulkASINProductResult,
  BulkAnalysisMode,
  BulkIngestStats,
  BulkJobResponse,
} from "@/lib/types";

const MAX_BYTES = 26_214_400;
const POLL_MS = 400;

export function BulkDueDiligence() {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<BulkIngestStats | null>(null);
  const [mode, setMode] = useState<BulkAnalysisMode>("standard");
  const [selection, setSelection] = useState<BulkAISelection>("high_priority");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [job, setJob] = useState<BulkJobResponse | null>(null);
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    if (!job || ["completed", "completed_with_errors", "failed"].includes(job.status)) {
      return;
    }
    const id = window.setInterval(() => {
      void fetchBulkJob(job.job_id)
        .then(setJob)
        .catch(() => undefined);
    }, POLL_MS);
    return () => window.clearInterval(id);
  }, [job]);

  async function onFile(next: File | null) {
    setError(null);
    setJob(null);
    setPreview(null);
    setFile(next);
    if (!next) {
      return;
    }
    if (next.size > MAX_BYTES) {
      setError("This file is larger than the 25 MB upload limit.");
      setFile(null);
      return;
    }
    setBusy(true);
    try {
      setPreview(await previewBulkFile(next));
    } catch (err) {
      setFile(null);
      setError(err instanceof ReportAnalysisError ? err.message : "This file could not be read.");
    } finally {
      setBusy(false);
    }
  }

  async function start() {
    if (!file) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setJob(await startBulkJob(file, mode, selection, 10));
    } catch (err) {
      setError(err instanceof ReportAnalysisError ? err.message : "This job could not be started.");
    } finally {
      setBusy(false);
    }
  }

  const done = job && (job.status === "completed" || job.status === "completed_with_errors");

  return (
    <div className="mx-auto w-full max-w-6xl space-y-6">
      <div>
        <p className="text-sm font-medium text-muted-foreground">Amazon Seller Intelligence</p>
        <h1 className="mt-1 text-3xl font-semibold tracking-tight">Bulk ASIN Due Diligence</h1>
        <p className="mt-2 max-w-3xl text-sm text-muted-foreground">
          Upload a CSV or Excel file of ASINs. This milestone runs against mock catalog fixtures
          only — it does not call Rainforest or OpenAI.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Upload CSV / XLSX</CardTitle>
          <CardDescription>
            Recognized columns: ASIN, asin, Amazon ASIN, Product ASIN, Amazon_ASIN.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <label
            className={cn(
              "flex cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed px-6 py-8 text-center text-sm",
              dragging ? "border-primary bg-muted/60" : "border-border bg-muted/30",
            )}
            onDragOver={(event: DragEvent) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event: DragEvent) => {
              event.preventDefault();
              setDragging(false);
              void onFile(event.dataTransfer.files[0] ?? null);
            }}
          >
            <input
              type="file"
              accept=".csv,.xlsx"
              className="hidden"
              onChange={(event: ChangeEvent<HTMLInputElement>) => {
                void onFile(event.target.files?.[0] ?? null);
                event.target.value = "";
              }}
            />
            <span className="font-medium">Drop a file here or click to browse</span>
            <span className="mt-1 text-muted-foreground">CSV or XLSX, up to 25 MB, 100 unique ASINs</span>
          </label>

          <div className="space-y-2">
            <p className="text-sm font-medium">Analysis Mode</p>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="radio"
                name="mode"
                checked={mode === "standard"}
                onChange={() => setMode("standard")}
              />
              Standard Due Diligence
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="radio"
                name="mode"
                checked={mode === "deep_ai"}
                onChange={() => setMode("deep_ai")}
              />
              Deep AI Due Diligence (mock AI only)
            </label>
            {mode === "deep_ai" ? (
              <select
                className="mt-1 rounded-md border border-input bg-background px-3 py-2 text-sm"
                value={selection}
                onChange={(event) => setSelection(event.target.value as BulkAISelection)}
              >
                <option value="high_priority">High-priority ASINs only</option>
                <option value="top_n">Top 10 weakest ASINs</option>
                <option value="all">All ASINs</option>
              </select>
            ) : null}
          </div>

          {preview ? (
            <div className="rounded-lg border border-border bg-background px-4 py-3 text-sm">
              <p className="font-medium">{preview.filename}</p>
              <p className="mt-1 text-muted-foreground">
                {preview.input_rows} rows found · {preview.valid_rows} valid · {preview.invalid_rows}{" "}
                invalid · {preview.duplicate_rows_removed} duplicates removed · {preview.unique_asins}{" "}
                unique ASINs
              </p>
            </div>
          ) : null}

          {error ? (
            <Alert variant="destructive">
              <AlertTitle>Upload problem</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}

          <Button onClick={() => void start()} disabled={!file || busy}>
            {busy ? <Loader2 className="animate-spin" /> : null}
            Start Analysis
          </Button>
        </CardContent>
      </Card>

      {job && !done && job.status !== "failed" ? <ProgressCard job={job} /> : null}
      {job?.status === "failed" ? (
        <Alert variant="destructive">
          <AlertTitle>Bulk analysis failed</AlertTitle>
          <AlertDescription>{job.error || "The job failed before a report could be produced."}</AlertDescription>
        </Alert>
      ) : null}
      {done && job ? <ReportView job={job} /> : null}
    </div>
  );
}

function ProgressCard({ job }: { job: BulkJobResponse }) {
  const pct = job.progress.total ? Math.round((job.progress.processed / job.progress.total) * 100) : 0;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Analyzing portfolio</CardTitle>
        <CardDescription>Testing with mock provider — no API credits consumed</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm">
          {job.progress.processed} / {job.progress.total} processed
        </p>
        <p className="text-xs text-muted-foreground">
          Cache hits: {job.progress.cache_hits} · Provider calls: {job.progress.provider_calls}
        </p>
        <div className="h-2 overflow-hidden rounded-full bg-muted">
          <div className="h-full bg-primary transition-[width]" style={{ width: `${pct}%` }} />
        </div>
      </CardContent>
    </Card>
  );
}

function ReportView({ job }: { job: BulkJobResponse }) {
  const summary = job.summary;
  const rows = useMemo(() => job.results, [job.results]);

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Bulk analysis complete</CardTitle>
          <CardDescription>
            {summary?.products_submitted ?? job.ingest.unique_asins} submitted ·{" "}
            {summary?.products_analyzed ?? 0} analyzed · {summary?.products_failed ?? 0} failed
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          <Button onClick={() => void downloadBulkReport(job.job_id)}>Download Excel</Button>
          <p className="w-full text-xs text-muted-foreground">{job.usage.note}</p>
        </CardContent>
      </Card>

      <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <SummaryTile label="Submitted" value={summary?.products_submitted} />
        <SummaryTile label="Analyzed" value={summary?.products_analyzed} />
        <SummaryTile label="Failed" value={summary?.products_failed} />
        <SummaryTile label="Average score" value={summary?.average_listing_score} />
        <SummaryTile label="High priority" value={summary?.high_priority_count} />
        <SummaryTile
          label="Med / Low"
          value={
            summary
              ? `${summary.medium_priority_count} / ${summary.low_priority_count}`
              : null
          }
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Portfolio table</CardTitle>
          <CardDescription>Sorted by priority, then lowest overall score.</CardDescription>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <ResultsTable rows={rows} />
        </CardContent>
      </Card>

      {job.attention.length > 0 ? (
        <Card>
          <CardHeader>
            <CardTitle>Products Requiring Attention</CardTitle>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <ResultsTable rows={job.attention} compact />
          </CardContent>
        </Card>
      ) : null}

      {job.failures.length > 0 ? (
        <Card>
          <CardHeader>
            <CardTitle>Failed / Invalid ASINs</CardTitle>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b text-xs text-muted-foreground">
                  <th className="py-2 pr-3">Row</th>
                  <th className="py-2 pr-3">Input ASIN</th>
                  <th className="py-2">Reason</th>
                </tr>
              </thead>
              <tbody>
                {job.failures.map((item, index) => (
                  <tr key={`${item.input_asin}-${index}`} className="border-b border-border/70">
                    <td className="py-2 pr-3 tabular-nums">{item.row ?? "—"}</td>
                    <td className="py-2 pr-3 font-mono text-xs">{item.input_asin || "—"}</td>
                    <td className="py-2">{item.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}

function SummaryTile({ label, value }: { label: string; value: number | string | null | undefined }) {
  return (
    <div className="rounded-lg border border-border bg-card px-3 py-3">
      <p className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="mt-1 text-lg font-semibold tabular-nums">{value ?? "—"}</p>
    </div>
  );
}

function ResultsTable({
  rows,
  compact = false,
}: {
  rows: BulkASINProductResult[];
  compact?: boolean;
}) {
  return (
    <table className="w-full min-w-[720px] text-left text-sm">
      <thead>
        <tr className="border-b text-xs text-muted-foreground">
          <th className="py-2 pr-3">ASIN</th>
          <th className="py-2 pr-3">Title</th>
          <th className="py-2 pr-3">Score</th>
          <th className="py-2 pr-3">Priority</th>
          {!compact ? (
            <>
              <th className="py-2 pr-3">Rating</th>
              <th className="py-2 pr-3">Reviews</th>
              <th className="py-2 pr-3">Images</th>
              <th className="py-2 pr-3">Title</th>
              <th className="py-2 pr-3">Bullets</th>
              <th className="py-2 pr-3">Desc</th>
              <th className="py-2 pr-3">Complete</th>
            </>
          ) : null}
          <th className="py-2 pr-3">Top issues</th>
          <th className="py-2">AI</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((item) => (
          <tr key={item.asin} className="border-b border-border/70 align-top">
            <td className="py-2 pr-3 font-mono text-xs">{item.asin}</td>
            <td className="max-w-[220px] py-2 pr-3">{item.product.title}</td>
            <td className="py-2 pr-3 tabular-nums">{item.listing_analysis.overall_score}</td>
            <td className="py-2 pr-3">
              <PriorityBadge priority={item.priority} />
            </td>
            {!compact ? (
              <>
                <td className="py-2 pr-3 tabular-nums">{item.product.rating ?? "—"}</td>
                <td className="py-2 pr-3 tabular-nums">{item.product.review_count ?? "—"}</td>
                <td className="py-2 pr-3 tabular-nums">{item.product.images.length}</td>
                <td className="py-2 pr-3 tabular-nums">{item.listing_analysis.sections.title.score}</td>
                <td className="py-2 pr-3 tabular-nums">{item.listing_analysis.sections.bullets.score}</td>
                <td className="py-2 pr-3 tabular-nums">{item.listing_analysis.sections.description.score}</td>
                <td className="py-2 pr-3 tabular-nums">{item.listing_analysis.sections.completeness.score}</td>
              </>
            ) : null}
            <td className="max-w-[240px] py-2 pr-3 text-xs text-muted-foreground">
              {item.listing_analysis.findings
                .slice(0, 2)
                .map((finding) => finding.code)
                .join(", ") || "—"}
            </td>
            <td className="py-2 text-xs">{aiLabel(item.ai_status)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function PriorityBadge({ priority }: { priority: "high" | "medium" | "low" }) {
  return (
    <Badge
      className={cn(
        priority === "high" && "border-transparent bg-destructive text-destructive-foreground",
        priority === "medium" && "border-transparent bg-amber-100 text-amber-900",
        priority === "low" && "border-transparent bg-secondary text-secondary-foreground",
      )}
    >
      {priority}
    </Badge>
  );
}

function aiLabel(status: BulkASINProductResult["ai_status"]): string {
  if (status === "mock") return "Mock AI";
  if (status === "cached") return "Cached";
  if (status === "skipped") return "Skipped";
  return "—";
}
