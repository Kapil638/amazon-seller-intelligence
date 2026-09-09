import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  InventoryApiError: class InventoryApiError extends Error {
    kind: string;
    constructor(message: string, kind = "unknown") {
      super(message);
      this.kind = kind;
    }
  },
  fetchInventoryHealthSummary: vi.fn(),
  fetchInventoryHealth: vi.fn(),
  fetchInventoryHealthEvidence: vi.fn(),
}));

import { SellerInventoryHealth } from "@/components/seller-inventory-health";
import { fetchInventoryHealth, fetchInventoryHealthEvidence, fetchInventoryHealthSummary } from "@/lib/api";
import type { InventoryHealthEvidence, InventoryHealthRow, InventoryHealthSummary } from "@/lib/types";

const THRESHOLDS = { low_coverage_days_threshold: 14, high_coverage_days_threshold: 90, min_eligible_window_days: 7, preferred_window_days: 30 };

function summary(overrides: Partial<InventoryHealthSummary> = {}): InventoryHealthSummary {
  return {
    marketplace_participation_id: "p1",
    total: 2,
    counts_by_inventory_state: { out_of_stock: 1, healthy_coverage: 1 },
    counts_by_demand_eligibility: { eligible: 1, no_eligible_sales_traffic_fact: 1 },
    formula_version: "12b6c-1.0.0",
    thresholds: THRESHOLDS,
    inventory_sync: { status: "succeeded", last_successful_synchronized_at: "2026-09-09T00:00:00.000Z" },
    sales_traffic_sync: { status: "never_synchronized", last_successful_synchronized_at: null },
    ...overrides,
  };
}

function row(overrides: Partial<InventoryHealthRow> = {}): InventoryHealthRow {
  return {
    inventory_id: "inv-1",
    seller_sku: "SKU-1",
    condition: "NewItem",
    asin: "B000000001",
    fnsku: "FN1",
    product_name: "Widget",
    is_active: true,
    fulfillable_quantity: 140,
    reserved_total_quantity: 0,
    inbound_working_quantity: null,
    inbound_shipped_quantity: null,
    inbound_receiving_quantity: null,
    unfulfillable_total_quantity: 0,
    researching_total_quantity: null,
    total_quantity: 140,
    demand_eligibility: "eligible",
    units_per_covered_day: 10,
    sales_window_start: "2026-08-01",
    sales_window_end: "2026-08-30",
    sales_covered_days: 30,
    fulfillable_days_of_cover: 14,
    potential_units: 140,
    potential_days_of_cover: 14,
    potential_units_incomplete_inputs: false,
    inventory_state: "healthy_coverage",
    freshness_state: "fresh",
    overlays: [],
    inventory_observed_at: "2026-09-09T00:00:00.000Z",
    amazon_last_updated_time: null,
    sales_traffic_ingestion_completed_at: "2026-09-08T00:00:00.000Z",
    ...overrides,
  };
}

function collection(items: InventoryHealthRow[] = [row()]) {
  return { items, total: items.length, offset: 0, limit: 25, formula_version: "12b6c-1.0.0", thresholds: THRESHOLDS };
}

describe("SellerInventoryHealth", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("shows the FBA-only disclosure", async () => {
    vi.mocked(fetchInventoryHealthSummary).mockResolvedValue(summary());
    vi.mocked(fetchInventoryHealth).mockResolvedValue(collection());
    render(<SellerInventoryHealth participationId="p1" />);
    await waitFor(() => expect(screen.getByText(/FBA-only/i)).toBeInTheDocument());
  });

  it("renders summary state counts, including a genuine zero state", async () => {
    vi.mocked(fetchInventoryHealthSummary).mockResolvedValue(
      summary({ counts_by_inventory_state: { out_of_stock: 0, healthy_coverage: 1 } }),
    );
    vi.mocked(fetchInventoryHealth).mockResolvedValue(collection());
    render(<SellerInventoryHealth participationId="p1" />);

    await waitFor(() => expect(screen.getByTestId("state-filter-out_of_stock")).toHaveTextContent("Out of fulfillable stock (0)"));
    expect(screen.getByTestId("state-filter-healthy_coverage")).toHaveTextContent("Healthy coverage (1)");
  });

  it("distinguishes a real zero velocity from a missing (null) one", async () => {
    vi.mocked(fetchInventoryHealthSummary).mockResolvedValue(summary());
    vi.mocked(fetchInventoryHealth).mockResolvedValue(
      collection([
        row({ inventory_id: "zero-demand", seller_sku: "SKU-ZERO", units_per_covered_day: 0, fulfillable_days_of_cover: null, overlays: ["no_recent_demand"] }),
        row({ inventory_id: "missing-demand", seller_sku: "SKU-MISSING", units_per_covered_day: null, demand_eligibility: "no_eligible_sales_traffic_fact", fulfillable_days_of_cover: null }),
      ]),
    );
    render(<SellerInventoryHealth participationId="p1" />);

    await waitFor(() => expect(screen.getByTestId("inventory-health-row-zero-demand")).toBeInTheDocument());
    const zeroRow = screen.getByTestId("inventory-health-row-zero-demand");
    // Intl.NumberFormat with maximumFractionDigits (no minimum) drops a
    // trailing zero — "0/day", not "0.0/day". The point under test is
    // that this is a rendered, real number, distinct from "—".
    expect(zeroRow).toHaveTextContent("0/day");
    const missingRow = screen.getByTestId("inventory-health-row-missing-demand");
    expect(missingRow).toHaveTextContent("—");
    expect(missingRow).toHaveTextContent(/No reliable demand evidence/i);
  });

  it("never renders an unavailable calculation as zero — days of cover shows a dash, not 0", async () => {
    vi.mocked(fetchInventoryHealthSummary).mockResolvedValue(summary());
    vi.mocked(fetchInventoryHealth).mockResolvedValue(
      collection([row({ fulfillable_days_of_cover: null, units_per_covered_day: null, demand_eligibility: "no_eligible_sales_traffic_fact" })]),
    );
    render(<SellerInventoryHealth participationId="p1" />);

    await waitFor(() => expect(screen.getByTestId("inventory-health-row-inv-1")).toBeInTheDocument());
    const rowEl = screen.getByTestId("inventory-health-row-inv-1");
    const dashesInRow = within(rowEl).getAllByText("—");
    expect(dashesInRow.length).toBeGreaterThanOrEqual(2); // velocity and days-of-cover columns
  });

  it("filters the visible rows when a state chip is clicked", async () => {
    vi.mocked(fetchInventoryHealthSummary).mockResolvedValue(summary());
    vi.mocked(fetchInventoryHealth).mockResolvedValue(
      collection([
        row({ inventory_id: "a", seller_sku: "SKU-OOS", inventory_state: "out_of_stock" }),
        row({ inventory_id: "b", seller_sku: "SKU-HEALTHY", inventory_state: "healthy_coverage" }),
      ]),
    );
    render(<SellerInventoryHealth participationId="p1" />);
    await waitFor(() => expect(screen.getByTestId("inventory-health-row-a")).toBeInTheDocument());

    await act(async () => {
      fireEvent.click(screen.getByTestId("state-filter-out_of_stock"));
    });

    expect(screen.getByTestId("inventory-health-row-a")).toBeInTheDocument();
    expect(screen.queryByTestId("inventory-health-row-b")).not.toBeInTheDocument();
  });

  it("opens the evidence panel and shows formula inputs on demand", async () => {
    vi.mocked(fetchInventoryHealthSummary).mockResolvedValue(summary());
    vi.mocked(fetchInventoryHealth).mockResolvedValue(collection());
    const evidence: InventoryHealthEvidence = {
      ...row(),
      inventory_ingestion_run_id: "run-inv-1",
      sales_traffic_ingestion_run_id: "run-sales-1",
      formula_version: "12b6c-1.0.0",
      thresholds: THRESHOLDS,
    };
    vi.mocked(fetchInventoryHealthEvidence).mockResolvedValue(evidence);
    render(<SellerInventoryHealth participationId="p1" />);
    await waitFor(() => expect(screen.getByTestId("inventory-health-row-inv-1")).toBeInTheDocument());

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^Evidence$/i }));
    });

    await waitFor(() => expect(screen.getByText("run-inv-1")).toBeInTheDocument());
    expect(screen.getByText("run-sales-1")).toBeInTheDocument();
    expect(screen.getByText("12b6c-1.0.0")).toBeInTheDocument();
  });

  it("shows a null amazon_last_updated_time as 'Not reported by Amazon', never as stale inventory", async () => {
    vi.mocked(fetchInventoryHealthSummary).mockResolvedValue(summary());
    vi.mocked(fetchInventoryHealth).mockResolvedValue(collection([row({ amazon_last_updated_time: null, freshness_state: "fresh" })]));
    const evidence: InventoryHealthEvidence = {
      ...row({ amazon_last_updated_time: null }),
      inventory_ingestion_run_id: "run-inv-1",
      sales_traffic_ingestion_run_id: null,
      formula_version: "12b6c-1.0.0",
      thresholds: THRESHOLDS,
    };
    vi.mocked(fetchInventoryHealthEvidence).mockResolvedValue(evidence);
    render(<SellerInventoryHealth participationId="p1" />);
    await waitFor(() => expect(screen.getByTestId("inventory-health-row-inv-1")).toBeInTheDocument());

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^Evidence$/i }));
    });

    await waitFor(() => expect(screen.getByText(/Not reported by Amazon/i)).toBeInTheDocument());
  });

  it("shows an error message when the summary fetch fails", async () => {
    vi.mocked(fetchInventoryHealthSummary).mockRejectedValue(new Error("boom"));
    vi.mocked(fetchInventoryHealth).mockRejectedValue(new Error("boom"));
    render(<SellerInventoryHealth participationId="p1" />);
    await waitFor(() => expect(screen.getByText("Inventory Health could not be loaded.")).toBeInTheDocument());
  });

  it("shows potential units and its days-of-cover pairing when every input is known", async () => {
    vi.mocked(fetchInventoryHealthSummary).mockResolvedValue(summary());
    vi.mocked(fetchInventoryHealth).mockResolvedValue(
      collection([row({ potential_units: 165, potential_days_of_cover: 16.5, potential_units_incomplete_inputs: false })]),
    );
    render(<SellerInventoryHealth participationId="p1" />);
    await waitFor(() => expect(screen.getByTestId("inventory-health-row-inv-1")).toBeInTheDocument());
    const rowEl = screen.getByTestId("inventory-health-row-inv-1");
    expect(rowEl).toHaveTextContent("165");
    expect(rowEl).toHaveTextContent("16.5d");
  });

  it("shows an explicit unknown warning — never a partial number — when an inbound quantity is unreported", async () => {
    vi.mocked(fetchInventoryHealthSummary).mockResolvedValue(summary());
    vi.mocked(fetchInventoryHealth).mockResolvedValue(
      collection([row({ potential_units: null, potential_days_of_cover: null, potential_units_incomplete_inputs: true })]),
    );
    render(<SellerInventoryHealth participationId="p1" />);
    await waitFor(() => expect(screen.getByText(/unknown — missing Amazon data/i)).toBeInTheDocument());
    // The strict null policy means there is never a number shown
    // alongside the warning — the potential-units cell itself must
    // render the same dash as any other unavailable value.
    const rowEl = screen.getByTestId("inventory-health-row-inv-1");
    expect(within(rowEl).getAllByText("—").length).toBeGreaterThanOrEqual(1);
  });
});
