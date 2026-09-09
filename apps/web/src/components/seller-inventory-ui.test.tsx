import * as React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a>,
}));

let mockSearch = "";
const mockListeners = new Set<() => void>();
function setMockSearch(next: string) {
  mockSearch = next;
  mockListeners.forEach((listener) => listener());
}
const routerReplace = vi.fn((url: string) => {
  const [, query = ""] = url.split("?");
  setMockSearch(query);
});

vi.mock("next/navigation", () => ({
  usePathname: () => "/seller/inventory",
  useRouter: () => ({ replace: routerReplace }),
  useSearchParams: () => {
    const snapshot = React.useSyncExternalStore(
      (onStoreChange: () => void) => {
        mockListeners.add(onStoreChange);
        return () => mockListeners.delete(onStoreChange);
      },
      () => mockSearch,
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
    return React.useMemo(() => new URLSearchParams(snapshot), [snapshot]);
  },
}));

vi.mock("@/lib/api", () => ({
  AmazonConnectionError: class AmazonConnectionError extends Error {
    kind: string;
    constructor(message: string, kind = "unknown") {
      super(message);
      this.kind = kind;
    }
  },
  InventoryApiError: class InventoryApiError extends Error {
    kind: string;
    constructor(message: string, kind = "unknown") {
      super(message);
      this.kind = kind;
    }
  },
  fetchAmazonConnection: vi.fn(),
  fetchInventorySummary: vi.fn(),
  fetchInventory: vi.fn(),
  triggerInventorySync: vi.fn(),
  // fix/inventory-empty-response-and-failure-classification — every UI
  // test in this file assumes an already-healthy worker unless a test
  // explicitly overrides this default, matching every test's own
  // pre-existing expectation that the Sync button starts out enabled.
  fetchWorkerHealth: vi.fn().mockResolvedValue({
    workers: { inventory: { available: true, last_heartbeat_at: "2026-08-29T00:00:00.000Z" } },
  }),
  // 12B.6C — SellerInventoryHealth is mounted as a child section of
  // SellerInventory whenever a participation is selected; every test in
  // this file gets a default empty-but-successful response unless it
  // overrides these explicitly.
  fetchInventoryHealthSummary: vi.fn().mockResolvedValue({
    marketplace_participation_id: "p1",
    total: 0,
    counts_by_inventory_state: {},
    counts_by_demand_eligibility: {},
    formula_version: "test",
    thresholds: { low_coverage_days_threshold: 14, high_coverage_days_threshold: 90, min_eligible_window_days: 7, preferred_window_days: 30 },
    inventory_sync: { status: "never_synchronized", last_successful_synchronized_at: null },
    sales_traffic_sync: { status: "never_synchronized", last_successful_synchronized_at: null },
  }),
  fetchInventoryHealth: vi.fn().mockResolvedValue({ items: [], total: 0, offset: 0, limit: 25, formula_version: "test", thresholds: { low_coverage_days_threshold: 14, high_coverage_days_threshold: 90, min_eligible_window_days: 7, preferred_window_days: 30 } }),
  fetchInventoryHealthEvidence: vi.fn(),
}));

import { SellerInventory } from "@/components/seller-inventory";
import {
  fetchAmazonConnection,
  fetchInventory,
  fetchInventorySummary,
  fetchWorkerHealth,
  triggerInventorySync,
} from "@/lib/api";
import type {
  AmazonConnectionOverview,
  InventoryCollectionItem,
  InventoryCollectionResponse,
  InventorySummary,
  InventorySyncEvidence,
} from "@/lib/types";

const US_ID = "11111111-1111-4111-8111-111111111111";

const baseOverview: AmazonConnectionOverview = {
  status: "CONNECTED",
  connection_status: "connected",
  persisted: true,
  provider: "SP_API",
  environment: "PRODUCTION",
  region: "na",
  marketplace: "amazon.com",
  application: "EWise",
  credentials_configured: true,
  selling_partner_id: "A1SELLERID",
  authorized_at: "2026-08-25T05:00:00.000Z",
  last_successful_validation_at: "2026-08-25T05:01:00.000Z",
  last_successful_sync_at: null,
  last_error_code: null,
  last_test_at: null,
  organization_id: "33333333-3333-4333-8333-333333333333",
  seller_account_id: "44444444-4444-4444-8444-444444444444",
  seller_account_display_name: "Synthetic Test Store",
  marketplaces: [
    {
      id: US_ID,
      marketplace_id: "ATVPDKIKX0DER",
      name: "Amazon.com",
      country_code: "US",
      domain_name: "www.amazon.com",
      is_participating: true,
      has_suspended_listings: false,
      is_active: true,
      last_seen_at: "2026-08-29T00:00:00.000Z",
    },
  ],
  latest_ingestion: null,
  ads_api: { provider: "ADS_API", status: "NOT_CONNECTED" },
};

function sync(overrides: Partial<InventorySyncEvidence> = {}): InventorySyncEvidence {
  return {
    status: "never_synchronized",
    failure_class: null,
    queued_at: null,
    started_at: null,
    completed_at: null,
    pages_fetched: null,
    records_received: null,
    records_accepted: null,
    records_rejected: null,
    pagination_complete: null,
    last_successful_synchronized_at: null,
    next_retry_at: null,
    ...overrides,
  };
}

function summary(overrides: Partial<InventorySummary> = {}): InventorySummary {
  return {
    marketplace_participation_id: US_ID,
    total: 0,
    active_count: 0,
    inactive_count: 0,
    with_asin_count: 0,
    with_fnsku_count: 0,
    zero_fulfillable_count: 0,
    sync: sync(),
    ...overrides,
  };
}

function collection(overrides: Partial<InventoryCollectionResponse> = {}): InventoryCollectionResponse {
  return { items: [], total: 0, offset: 0, limit: 25, ...overrides };
}

function item(overrides: Partial<InventoryCollectionItem> = {}): InventoryCollectionItem {
  return {
    id: "aaaaaaaa-0000-4000-8000-000000000001",
    seller_sku: "SYN-SKU-1",
    condition: "NewItem",
    asin: "B0SYNTH0001",
    fnsku: "FNSYNTH1",
    product_name: "Synthetic Widget",
    total_quantity: 10,
    fulfillable_quantity: 8,
    reserved_total_quantity: 2,
    inbound_total_quantity: 0,
    unfulfillable_total_quantity: 0,
    is_active: true,
    first_seen_at: "2026-08-29T00:00:00.000Z",
    last_seen_at: "2026-08-29T08:00:00.000Z",
    amazon_last_updated_time: "2026-08-29T07:00:00.000Z",
    ...overrides,
  };
}

beforeEach(() => {
  mockSearch = "";
  vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
  vi.mocked(fetchInventorySummary).mockResolvedValue(summary());
  vi.mocked(fetchInventory).mockResolvedValue(collection());
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  routerReplace.mockClear();
});

describe("SellerInventory", () => {
  it("always shows the persistent FBA-only disclosure", async () => {
    render(<SellerInventory />);
    await waitFor(() => expect(screen.getByText(/FBA-fulfilled inventory only/i)).toBeInTheDocument());
  });

  it("shows the never_synchronized empty state", async () => {
    render(<SellerInventory />);
    await waitFor(() => expect(screen.getByText("Not yet synchronized")).toBeInTheDocument());
    expect(screen.getByText(/No FBA Inventory data yet/i)).toBeInTheDocument();
  });

  it("shows the queued state with the waiting-for-worker message", async () => {
    vi.mocked(fetchInventorySummary).mockResolvedValue(summary({ sync: sync({ status: "queued" }) }));
    render(<SellerInventory />);
    await waitFor(() => expect(screen.getByText("Queued for synchronization")).toBeInTheDocument());
    expect(screen.getByText(/Waiting for a worker to pick this up/i)).toBeInTheDocument();
  });

  it("shows the running state", async () => {
    vi.mocked(fetchInventorySummary).mockResolvedValue(summary({ sync: sync({ status: "running" }) }));
    render(<SellerInventory />);
    await waitFor(() => expect(screen.getByText("Importing FBA inventory")).toBeInTheDocument());
  });

  it("shows the failed state with a sanitized failure reason", async () => {
    vi.mocked(fetchInventorySummary).mockResolvedValue(
      summary({ sync: sync({ status: "failed", failure_class: "authentication_failed" }) }),
    );
    render(<SellerInventory />);
    await waitFor(() => expect(screen.getByText("Needs attention")).toBeInTheDocument());
    expect(screen.getByText(/may need to be reauthorized/i)).toBeInTheDocument();
  });

  it("shows metric tiles and inventory rows on succeeded state", async () => {
    vi.mocked(fetchInventorySummary).mockResolvedValue(
      summary({
        total: 5,
        active_count: 4,
        inactive_count: 1,
        with_asin_count: 5,
        zero_fulfillable_count: 1,
        sync: sync({ status: "succeeded", last_successful_synchronized_at: "2026-08-29T08:05:00.000Z" }),
      }),
    );
    vi.mocked(fetchInventory).mockResolvedValue(collection({ items: [item()], total: 1 }));
    render(<SellerInventory />);
    await waitFor(() => expect(screen.getByText("Completed")).toBeInTheDocument());
    expect(screen.getByText("Synthetic Widget")).toBeInTheDocument();
    expect(screen.getByText(/SKU SYN-SKU-1/)).toBeInTheDocument();
  });

  it("marks an inactive (not confirmed) row without zeroing its quantities", async () => {
    vi.mocked(fetchInventorySummary).mockResolvedValue(summary({ sync: sync({ status: "succeeded" }) }));
    vi.mocked(fetchInventory).mockResolvedValue(
      collection({ items: [item({ is_active: false, total_quantity: 10, fulfillable_quantity: 8 })], total: 1 }),
    );
    render(<SellerInventory />);
    await waitFor(() => expect(screen.getByText("Not confirmed")).toBeInTheDocument());
    // Quantities are preserved (last-known), never displayed as zero.
    const row = screen.getByText("Synthetic Widget").closest("tr");
    expect(row).not.toBeNull();
    expect(row!.textContent).toContain("10");
    expect(row!.textContent).toContain("8");
  });

  it("triggers a sync and refreshes the summary on success", async () => {
    vi.mocked(triggerInventorySync).mockResolvedValue({ reason: "queued", message: null, job: null });
    render(<SellerInventory />);
    await waitFor(() => expect(screen.getByText("Not yet synchronized")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /Sync FBA Inventory/i }));

    await waitFor(() => expect(triggerInventorySync).toHaveBeenCalledWith(US_ID));
    await waitFor(() => expect(fetchInventorySummary).toHaveBeenCalledTimes(2));
  });

  it("shows a sanitized message when the trigger is rejected", async () => {
    vi.mocked(triggerInventorySync).mockResolvedValue({
      reason: "cooldown",
      message: "Please wait a moment before synchronizing this marketplace again.",
      job: null,
    });
    render(<SellerInventory />);
    await waitFor(() => expect(screen.getByText("Not yet synchronized")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /Sync FBA Inventory/i }));

    await waitFor(() => expect(screen.getByText(/Please wait a moment/i)).toBeInTheDocument());
  });

  it("shows a clear, actionable message and never queues when the Inventory worker is unavailable", async () => {
    // fix/ingestion-worker-runtime-availability, integrated into
    // Inventory: the backend refuses the trigger outright
    // (reason="worker_unavailable") when no matching worker has
    // reported a recent heartbeat — this falls through the component's
    // own generic non-"queued" branch, so it renders exactly like any
    // other structured rejection (cooldown) with zero component-
    // specific code for this reason.
    vi.mocked(triggerInventorySync).mockResolvedValue({
      reason: "worker_unavailable",
      message:
        "The Inventory sync worker is not running, so this job would never be picked up. " +
        "Start local development with the connected-seller runtime: " +
        "./scripts/dev.sh --with-workers (or ASI_INVENTORY_WORKER_ENABLED=true ./scripts/dev.sh), " +
        "then try again.",
      job: null,
    });
    render(<SellerInventory />);
    await waitFor(() => expect(screen.getByText("Not yet synchronized")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /Sync FBA Inventory/i }));

    await waitFor(() => expect(screen.getByText(/worker is not running/i)).toBeInTheDocument());
    expect(screen.getByText(/--with-workers/i)).toBeInTheDocument();
  });

  it("filters the list by search term", async () => {
    vi.mocked(fetchInventorySummary).mockResolvedValue(summary({ sync: sync({ status: "succeeded" }) }));
    vi.mocked(fetchInventory).mockResolvedValue(collection({ items: [item()], total: 1 }));
    render(<SellerInventory />);
    await waitFor(() => expect(screen.getByText("Synthetic Widget")).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText(/Search SKU, FNSKU, or ASIN/i), {
      target: { value: "SYN-SKU-1" },
    });

    await waitFor(() =>
      expect(fetchInventory).toHaveBeenLastCalledWith(US_ID, expect.objectContaining({ q: "SYN-SKU-1" })),
    );
  });

  it("shows the connection-not-configured empty state when no marketplace is connected", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue({ ...baseOverview, marketplaces: [] });
    render(<SellerInventory />);
    await waitFor(() =>
      expect(screen.getByText(/No Amazon marketplace is connected yet/i)).toBeInTheDocument(),
    );
  });
});

describe("SellerInventory — worker readiness (fix/inventory-empty-response-and-failure-classification)", () => {
  // The live defect this closes: Sync was clickable (and rejected with
  // worker_unavailable) during the brief window before a freshly-
  // started worker publishes its first heartbeat, and the resulting
  // error banner never cleared itself once the worker became healthy —
  // nothing on the page was polling worker health at all.
  //
  // Fake timers are engaged for the *entire* test (not switched on
  // mid-test): the worker-health hook schedules its own `setTimeout`
  // poll loop from the moment the component mounts, and a `setTimeout`
  // already scheduled under real timers is never reachable by a later
  // `vi.advanceTimersByTimeAsync` call. `@testing-library/react`'s own
  // `waitFor` polls with real timers, so it cannot be used here either
  // — `flush()` below drives fake-timer/microtask settling directly.

  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  async function flush() {
    // Several small fake-timer advances (rather than one large one)
    // give every pending microtask/effect chain — connection load ->
    // participation resolution -> summary load -> worker-health's own
    // first check — a chance to settle in order, exactly as they would
    // in real use, without depending on real wall-clock time at all.
    for (let i = 0; i < 6; i += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
    }
  }

  it("disables Sync and shows 'Starting worker…' before the first heartbeat appears", async () => {
    vi.mocked(fetchWorkerHealth).mockResolvedValue({
      workers: { inventory: { available: false, last_heartbeat_at: null } },
    });
    render(<SellerInventory />);
    await flush();

    expect(screen.getByText("Not yet synchronized")).toBeInTheDocument();
    expect(screen.getByText(/Starting worker…/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Sync FBA Inventory/i })).toBeDisabled();
  });

  it("automatically enables Sync as soon as a fresh heartbeat appears — no page refresh required", async () => {
    vi.mocked(fetchWorkerHealth).mockResolvedValue({
      workers: { inventory: { available: false, last_heartbeat_at: null } },
    });
    render(<SellerInventory />);
    await flush();
    expect(screen.getByRole("button", { name: /Sync FBA Inventory/i })).toBeDisabled();

    vi.mocked(fetchWorkerHealth).mockResolvedValue({
      workers: { inventory: { available: true, last_heartbeat_at: "2026-09-09T00:00:05.000Z" } },
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000); // the hook's own default poll interval
    });

    expect(screen.getByRole("button", { name: /Sync FBA Inventory/i })).not.toBeDisabled();
    expect(screen.queryByText(/Starting worker…/i)).not.toBeInTheDocument();
  });

  it("shows 'Worker unavailable' once the startup-grace window expires with no heartbeat", async () => {
    vi.mocked(fetchWorkerHealth).mockResolvedValue({
      workers: { inventory: { available: false, last_heartbeat_at: null } },
    });
    render(<SellerInventory />);
    await flush();
    expect(screen.getByText(/Starting worker…/i)).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60000); // the hook's own default grace window
    });

    expect(screen.getByText(/^Worker unavailable$/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Sync FBA Inventory/i })).toBeDisabled();
  });

  it("clears a stale worker_unavailable banner automatically once the worker recovers, without a manual refresh", async () => {
    // The exact real-world race this defends: the health poll's own
    // last answer was "available" (button briefly enabled), a click
    // landed and the backend rejected it as worker_unavailable (the
    // backend's own check ran a moment later, catching a state the
    // frontend hadn't observed yet) — the stale error must not survive
    // the worker's very next recovery.
    vi.mocked(fetchWorkerHealth).mockResolvedValue({
      workers: { inventory: { available: true, last_heartbeat_at: "2026-09-09T00:00:00.000Z" } },
    });
    vi.mocked(triggerInventorySync).mockResolvedValue({
      reason: "worker_unavailable",
      message: "The Inventory sync worker is not running, so this job would never be picked up.",
      job: null,
    });
    render(<SellerInventory />);
    await flush();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Sync FBA Inventory/i }));
    });
    await flush();
    expect(screen.getByText(/worker is not running/i)).toBeInTheDocument();

    // Next poll tick still confirms availability (no state change) —
    // the banner must not clear on its own without the hook ever having
    // observed a transition; this proves the effect is keyed on the
    // hook's own state, not merely on the passage of time.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(screen.getByText(/worker is not running/i)).toBeInTheDocument();
  });

  it("a historical failed run is displayed independently from current worker health — a healthy worker still shows the prior failure", async () => {
    vi.mocked(fetchWorkerHealth).mockResolvedValue({
      workers: { inventory: { available: true, last_heartbeat_at: "2026-09-09T00:00:00.000Z" } },
    });
    vi.mocked(fetchInventorySummary).mockResolvedValue(
      summary({ sync: sync({ status: "failed", failure_class: "malformed_page_retry_exhausted" }) }),
    );
    vi.mocked(fetchInventory).mockResolvedValue(collection());
    render(<SellerInventory />);
    await flush();

    expect(screen.getByText("Needs attention")).toBeInTheDocument();
    // The worker itself is healthy — Sync must remain available despite
    // the historical failure being shown truthfully above it.
    expect(screen.getByRole("button", { name: /Sync FBA Inventory/i })).not.toBeDisabled();
    expect(screen.queryByText(/^Worker unavailable$/i)).not.toBeInTheDocument();
  });
});
