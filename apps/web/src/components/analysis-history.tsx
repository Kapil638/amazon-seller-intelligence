"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import Link from "next/link";
import { Loader2, MoreHorizontal } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { EmptyState, PageHeader, Panel } from "@/components/ui/layout";
import {
  deleteSavedAnalysis,
  downloadSavedAnalysisPdf,
  fetchSavedAnalyses,
  generateSavedAnalysisPdf,
  ProductLookupError,
} from "@/lib/api";
import type { SavedAnalysisSummary } from "@/lib/types";

const PAGE_SIZE = 20;

function formatDate(value: string | null): string {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
  }).format(date);
}

function sourceLabel(source: string | null): string {
  if (source === "rainforest") return "Rainforest";
  if (source === "mock") return "Mock catalog";
  if (source === "manual") return "Manual";
  if (source === "amazon_public") return "Amazon.in public";
  return source || "—";
}

function statusLabel(status: string): string {
  if (status === "complete") return "Complete";
  if (status === "partial") return "Partial";
  if (status === "failed") return "Failed";
  if (status === "running") return "Running";
  if (status === "pending") return "Pending";
  return status;
}

export function AnalysisHistory() {
  const [items, setItems] = useState<SavedAnalysisSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pdfBusyId, setPdfBusyId] = useState<string | null>(null);
  const [menuOpenId, setMenuOpenId] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<SavedAnalysisSummary | null>(null);
  const [deleting, setDeleting] = useState(false);

  const load = useCallback(async (nextOffset: number) => {
    setLoading(true);
    setError(null);
    try {
      const page = await fetchSavedAnalyses({ offset: nextOffset, limit: PAGE_SIZE });
      setItems(page.items);
      setTotal(page.total);
      setOffset(page.offset);
    } catch (err) {
      setItems([]);
      setTotal(0);
      if (err instanceof ProductLookupError) {
        setError(err.message);
      } else {
        setError("Saved analyses could not be loaded.");
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(0);
  }, [load]);

  const handlePdf = async (item: SavedAnalysisSummary) => {
    setPdfBusyId(item.report_id);
    setError(null);
    try {
      const generated = await generateSavedAnalysisPdf(item.report_id);
      await downloadSavedAnalysisPdf(item.report_id, generated.filename);
      setNotice(
        generated.reused
          ? "PDF downloaded."
          : "PDF generated and downloaded.",
      );
    } catch (err) {
      setError(
        err instanceof ProductLookupError
          ? err.message
          : "PDF could not be generated. Please try again.",
      );
    } finally {
      setPdfBusyId(null);
    }
  };

  const confirmDelete = async () => {
    if (!pendingDelete) {
      return;
    }
    const target = pendingDelete;
    setDeleting(true);
    setError(null);
    try {
      await deleteSavedAnalysis(target.report_id);
      const remaining = items.filter((row) => row.report_id !== target.report_id);
      const nextTotal = Math.max(total - 1, 0);
      setPendingDelete(null);
      setNotice("Report removed from Analysis History.");
      if (remaining.length === 0 && offset > 0) {
        await load(Math.max(offset - PAGE_SIZE, 0));
      } else {
        setItems(remaining);
        setTotal(nextTotal);
      }
    } catch (err) {
      setError(
        err instanceof ProductLookupError
          ? err.message
          : "This report could not be deleted.",
      );
    } finally {
      setDeleting(false);
    }
  };

  const pageStart = total === 0 ? 0 : offset + 1;
  const pageEnd = Math.min(offset + items.length, total);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Saved Analyses"
        description="Historical ASIN reports. Opening, exporting, or deleting a saved analysis does not refresh Amazon or AI data. Seller Central CSV uploads stay on Seller Reports."
      />

      {notice ? (
        <Alert>
          <AlertTitle>Done</AlertTitle>
          <AlertDescription>{notice}</AlertDescription>
        </Alert>
      ) : null}

      {error ? (
        <Alert variant="destructive">
          <AlertTitle>History unavailable</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}

      {loading ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading saved analyses…
        </div>
      ) : null}

      {!loading && !error && items.length === 0 ? (
        <EmptyState
          title="No saved analyses yet."
          description="Analyze an ASIN to create your first report."
        />
      ) : null}

      {!loading && items.length > 0 ? (
        <Panel className="overflow-x-auto">
          <table className="w-full min-w-[52rem] text-left text-sm">
            <thead className="border-b border-border text-xs text-muted-foreground">
              <tr>
                <th className="px-4 py-3 font-medium">Product</th>
                <th className="px-4 py-3 font-medium">ASIN</th>
                <th className="px-4 py-3 font-medium">Listing Score</th>
                <th className="px-4 py-3 font-medium">AI Strategy</th>
                <th className="px-4 py-3 font-medium">Image Analysis</th>
                <th className="px-4 py-3 font-medium">Source</th>
                <th className="px-4 py-3 font-medium">Analyzed</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 text-right font-medium whitespace-nowrap">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {items.map((item) => (
                <tr key={item.report_id} className="align-top">
                  <td className="px-4 py-3">
                    <p className="font-medium leading-5">
                      {item.product_title?.trim() || item.display_name || "Untitled listing"}
                    </p>
                    {item.brand ? (
                      <p className="mt-0.5 text-xs text-muted-foreground">{item.brand}</p>
                    ) : null}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs">{item.asin}</td>
                  <td className="px-4 py-3 tabular-nums">
                    {item.listing_quality_score != null ? (
                      <div>
                        <p>Standard {item.listing_quality_score}</p>
                        {item.custom_listing_quality_score != null ? (
                          <p className="text-xs text-muted-foreground">
                            Custom {item.custom_listing_quality_score}
                            {item.scoring_profile_name ? ` · ${item.scoring_profile_name}` : ""}
                          </p>
                        ) : null}
                      </div>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="px-4 py-3">{item.has_ai_strategy ? "Yes" : "No"}</td>
                  <td className="px-4 py-3">{item.has_image_intelligence ? "Yes" : "No"}</td>
                  <td className="px-4 py-3">{sourceLabel(item.source)}</td>
                  <td className="px-4 py-3">{formatDate(item.created_at)}</td>
                  <td className="px-4 py-3">{statusLabel(item.status)}</td>
                  <td className="w-px px-4 py-3 align-top text-right whitespace-nowrap">
                    <RowActions
                      item={item}
                      pdfBusy={pdfBusyId === item.report_id}
                      menuOpen={menuOpenId === item.report_id}
                      onToggleMenu={() =>
                        setMenuOpenId((current) =>
                          current === item.report_id ? null : item.report_id,
                        )
                      }
                      onCloseMenu={() => setMenuOpenId(null)}
                      onPdf={() => void handlePdf(item)}
                      onDelete={() => {
                        setMenuOpenId(null);
                        setPendingDelete(item);
                      }}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="flex items-center justify-between gap-3 border-t border-border px-4 py-3 text-sm text-muted-foreground">
            <p>
              {pageStart}–{pageEnd} of {total}
            </p>
            <div className="flex gap-2">
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={offset <= 0}
                onClick={() => void load(Math.max(offset - PAGE_SIZE, 0))}
              >
                Previous
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={offset + PAGE_SIZE >= total}
                onClick={() => void load(offset + PAGE_SIZE)}
              >
                Next
              </Button>
            </div>
          </div>
        </Panel>
      ) : null}

      {pendingDelete ? (
        <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4 sm:items-center">
          <Panel className="w-full max-w-md space-y-4 p-5">
            <h3 className="text-base font-semibold">Delete saved analysis?</h3>
            <p className="text-sm text-muted-foreground">
              This report will be removed from your Analysis History. The historical record will
              remain archived in the system.
            </p>
            <div className="flex justify-end gap-2">
              <Button
                type="button"
                variant="outline"
                disabled={deleting}
                onClick={() => setPendingDelete(null)}
              >
                Cancel
              </Button>
              <Button
                type="button"
                variant="destructive"
                disabled={deleting}
                onClick={() => void confirmDelete()}
              >
                {deleting ? "Deleting…" : "Delete Report"}
              </Button>
            </div>
          </Panel>
        </div>
      ) : null}
    </div>
  );
}

function RowActions({
  item,
  pdfBusy,
  menuOpen,
  onToggleMenu,
  onCloseMenu,
  onPdf,
  onDelete,
}: {
  item: SavedAnalysisSummary;
  pdfBusy: boolean;
  menuOpen: boolean;
  onToggleMenu: () => void;
  onCloseMenu: () => void;
  onPdf: () => void;
  onDelete: () => void;
}) {
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [coords, setCoords] = useState<{ top: number; left: number } | null>(null);

  useEffect(() => {
    if (!menuOpen || !buttonRef.current) {
      return;
    }
    const place = () => {
      if (!buttonRef.current) {
        return;
      }
      const rect = buttonRef.current.getBoundingClientRect();
      const menuWidth = 176;
      const left = Math.min(
        Math.max(8, rect.right - menuWidth),
        window.innerWidth - menuWidth - 8,
      );
      setCoords({ top: rect.bottom + 6, left });
    };
    place();
    const onPointer = (event: MouseEvent) => {
      const target = event.target as Node;
      if (buttonRef.current?.contains(target) || menuRef.current?.contains(target)) {
        return;
      }
      onCloseMenu();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onCloseMenu();
      }
    };
    window.addEventListener("mousedown", onPointer);
    window.addEventListener("keydown", onKey);
    window.addEventListener("resize", onCloseMenu);
    window.addEventListener("scroll", onCloseMenu, true);
    return () => {
      window.removeEventListener("mousedown", onPointer);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", onCloseMenu);
      window.removeEventListener("scroll", onCloseMenu, true);
    };
  }, [menuOpen, onCloseMenu]);

  return (
    <div className="inline-flex items-center justify-end gap-2">
      <Button asChild size="sm" variant="outline" className="shrink-0">
        <Link href={`/history/${item.report_id}`}>Open</Link>
      </Button>
      <Button
        ref={buttonRef}
        type="button"
        size="icon"
        variant="outline"
        className="h-8 w-8 shrink-0"
        aria-haspopup="menu"
        aria-expanded={menuOpen}
        aria-label="More actions"
        disabled={pdfBusy}
        onClick={onToggleMenu}
      >
        {pdfBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <MoreHorizontal className="h-4 w-4" />}
      </Button>
      {menuOpen && coords
        ? createPortal(
            <div
              ref={menuRef}
              role="menu"
              style={{ top: coords.top, left: coords.left }}
              className="fixed z-[80] w-44 rounded-md border border-border bg-surface py-1 shadow-[var(--shadow-sm)]"
            >
              <button
                type="button"
                role="menuitem"
                className="flex w-full items-center px-3 py-2 text-left text-sm hover:bg-surface-subtle"
                onClick={() => {
                  onCloseMenu();
                  onPdf();
                }}
              >
                Generate PDF
              </button>
              <button
                type="button"
                role="menuitem"
                className="flex w-full items-center px-3 py-2 text-left text-sm text-destructive hover:bg-surface-subtle"
                onClick={onDelete}
              >
                Delete Report
              </button>
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}
