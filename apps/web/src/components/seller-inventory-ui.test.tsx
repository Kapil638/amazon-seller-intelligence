import * as React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
}));

import { SellerInventory } from "@/components/seller-inventory";
import {
  fetchAmazonConnection,
  fetchInventory,
  fetchInventorySummary,
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
