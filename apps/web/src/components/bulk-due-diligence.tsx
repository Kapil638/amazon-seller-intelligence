"use client";

import { useEffect, useMemo, useState, type ChangeEvent, type DragEvent } from "react";
import { Loader2 } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Kpi, PageHeader, Panel } from "@/components/ui/layout";
import { SeverityLabel } from "@/components/ui/score";
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
  const idle = !job;

  return (
    <div className={idle ? "flex min-h-[calc(100dvh-7.5rem)] flex-col items-center justify-center" : "space-y-8"}>
      <div className={idle ? "w-full max-w-2xl space-y-6" : "space-y-8"}>
      <PageHeader
        align={idle ? "center" : "start"}
        title="Bulk product intelligence"
        description="Upload an Excel or CSV containing Amazon ASINs. Current configuration uses mock catalog and mock AI only — live Rainforest and OpenAI are not called."
      />

      <Panel className="p-5">
        <div className={cn("mb-4 space-y-1", idle && "text-center")}>
          <h2 className="text-[0.95rem] font-semibold">Upload file</h2>
          <p className="text-sm text-muted-foreground">
            Recognized columns: ASIN, asin, Amazon ASIN, Product ASIN, Amazon_ASIN.
          </p>
        </div>
        <div className="space-y-4">
          <label
            className={cn(
              "flex cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed px-6 py-10 text-center text-sm transition-colors duration-200",
              dragging ? "border-primary bg-surface-subtle" : "border-border bg-surface-subtle/50",
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

          <fieldset className={cn("space-y-2", idle && "text-center")}>
            <legend className="text-sm font-medium">Analysis mode</legend>
            <label className={cn("flex items-center gap-2 text-sm", idle && "justify-center")}>
              <input
                type="radio"
                name="mode"
                checked={mode === "standard"}
                onChange={() => setMode("standard")}
              />
              Standard due diligence
            </label>
            <label className={cn("flex items-center gap-2 text-sm", idle && "justify-center")}>
              <input
                type="radio"
                name="mode"
                checked={mode === "deep_ai"}
                onChange={() => setMode("deep_ai")}
              />
              Deep AI due diligence (mock AI only)
            </label>
            {mode === "deep_ai" ? (
              <select
                className={cn(
                  "mt-1 rounded-md border border-input bg-surface px-3 py-2 text-sm",
                  idle && "mx-auto block",
                )}
                value={selection}
                onChange={(event) => setSelection(event.target.value as BulkAISelection)}
              >
                <option value="high_priority">High-priority ASINs only</option>
                <option value="top_n">Top 10 weakest ASINs</option>
                <option value="all">All ASINs</option>
              </select>
            ) : null}
          </fieldset>

          {preview ? (
            <div className="grid gap-4 rounded-md bg-surface-subtle px-4 py-3 sm:grid-cols-5">
              <Kpi label="Rows" value={preview.input_rows} />
              <Kpi label="Valid" value={preview.valid_rows} />
              <Kpi label="Invalid" value={preview.invalid_rows} />
              <Kpi label="Duplicates removed" value={preview.duplicate_rows_removed} />
              <Kpi label="Unique ASINs" value={preview.unique_asins} />
            </div>
          ) : null}

          {error ? (
            <Alert variant="destructive">
              <AlertTitle>Upload problem</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}

          <div className={idle ? "flex justify-center" : undefined}>
            <Button onClick={() => void start()} disabled={!file || busy}>
              {busy ? <Loader2 className="animate-spin" /> : null}
              Start analysis
            </Button>
          </div>
        </div>
      </Panel>
      </div>

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
    <Panel className="p-5">
      <h2 className="text-[0.95rem] font-semibold">Analyzing portfolio</h2>
      <p className="mt-1 text-sm text-muted-foreground">
        Testing with mock provider — no API credits consumed
      </p>
      <div className="mt-5 grid gap-4 sm:grid-cols-4">
        <Kpi label="Processed" value={`${job.progress.processed} / ${job.progress.total}`} />
        <Kpi label="Cached" value={job.progress.cache_hits} />
        <Kpi label="Provider calls" value={job.progress.provider_calls} />
        <Kpi label="Failed" value={job.progress.failed} />
      </div>
      <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-surface-subtle">
        <div className="h-full bg-primary transition-[width] duration-200" style={{ width: `${pct}%` }} />
      </div>
      <p className="mt-2 text-xs text-muted-foreground">{pct}% complete</p>
    </Panel>
  );
}

function ReportView({ job }: { job: BulkJobResponse }) {
  const summary = job.summary;
  const rows = useMemo(() => job.results, [job.results]);

  return (
    <div className="space-y-6">
      <Panel className="p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-[0.95rem] font-semibold">Bulk analysis complete</h2>
            <p className="mt-1 text-sm text-muted-foreground">{job.usage.note}</p>
          </div>
          <Button variant="outline" onClick={() => void downloadBulkReport(job.job_id)}>
            Download Excel
          </Button>
        </div>
        <div className="mt-5 grid gap-4 sm:grid-cols-3 lg:grid-cols-6">
          <Kpi label="Submitted" value={summary?.products_submitted ?? job.ingest.unique_asins} />
          <Kpi label="Analyzed" value={summary?.products_analyzed ?? 0} />
          <Kpi label="Failed" value={summary?.products_failed ?? 0} />
          <Kpi label="Average score" value={summary?.average_listing_score ?? "—"} />
          <Kpi label="High priority" value={summary?.high_priority_count ?? 0} />
          <Kpi
            label="Med / Low"
            value={summary ? `${summary.medium_priority_count} / ${summary.low_priority_count}` : "—"}
          />
        </div>
      </Panel>

      <Panel className="overflow-hidden p-5">
        <h2 className="mb-1 text-[0.95rem] font-semibold">Portfolio</h2>
        <p className="mb-4 text-sm text-muted-foreground">Sorted by priority, then lowest overall score.</p>
        <ResultsTable rows={rows} />
      </Panel>

      {job.attention.length > 0 ? (
        <Panel className="overflow-hidden p-5">
          <h2 className="mb-4 text-[0.95rem] font-semibold">Products requiring attention</h2>
          <ResultsTable rows={job.attention} compact />
        </Panel>
      ) : null}

      {job.failures.length > 0 ? (
        <Panel className="overflow-hidden p-5">
          <h2 className="mb-4 text-[0.95rem] font-semibold">Failed / invalid ASINs</h2>
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th className="num">Row</th>
                  <th>Input ASIN</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {job.failures.map((item, index) => (
                  <tr key={`${item.input_asin}-${index}`}>
                    <td className="num">{item.row ?? "—"}</td>
                    <td className="font-mono text-xs">{item.input_asin || "—"}</td>
                    <td>{item.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      ) : null}
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
    <div className="overflow-x-auto">
      <table className="data-table min-w-[720px]">
        <thead>
          <tr>
            <th>ASIN</th>
            <th>Title</th>
            <th className="num">Score</th>
            <th>Priority</th>
            {!compact ? (
              <>
                <th className="num">Rating</th>
                <th className="num">Reviews</th>
                <th className="num">Images</th>
                <th className="num">Title</th>
                <th className="num">Bullets</th>
                <th className="num">Desc</th>
                <th className="num">Complete</th>
              </>
            ) : null}
            <th>Top issues</th>
            <th>AI</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((item) => (
            <tr key={item.asin}>
              <td className="font-mono text-xs">{item.asin}</td>
              <td className="max-w-[220px]">{item.product.title}</td>
              <td className="num">{item.listing_analysis.overall_score}</td>
              <td>
                <SeverityLabel severity={item.priority} />
              </td>
              {!compact ? (
                <>
                  <td className="num">{item.product.rating ?? "—"}</td>
                  <td className="num">{item.product.review_count ?? "—"}</td>
                  <td className="num">{item.product.images.length}</td>
                  <td className="num">{item.listing_analysis.sections.title.score}</td>
                  <td className="num">{item.listing_analysis.sections.bullets.score}</td>
                  <td className="num">{item.listing_analysis.sections.description.score}</td>
                  <td className="num">{item.listing_analysis.sections.completeness.score}</td>
                </>
              ) : null}
              <td className="max-w-[240px] text-muted-foreground">
                {item.listing_analysis.findings
                  .slice(0, 2)
                  .map((finding) => finding.code)
                  .join(", ") || "—"}
              </td>
              <td>{aiLabel(item.ai_status)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function aiLabel(status: BulkASINProductResult["ai_status"]): string {
  if (status === "mock") return "Mock AI";
  if (status === "cached") return "Cached";
  if (status === "skipped") return "Skipped";
  return "—";
}
