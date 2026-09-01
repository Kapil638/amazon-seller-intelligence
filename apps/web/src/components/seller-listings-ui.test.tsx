import * as React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a>,
}));

// A minimal, reactive fake of next/navigation's URL-state hooks. `replace`
// updates the shared search string and notifies subscribers via
// useSyncExternalStore, so components under test see the new params on
// their next render exactly like a real client-side navigation would.
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
  usePathname: () => "/seller-listings",
  useRouter: () => ({ replace: routerReplace }),
  useSearchParams: () => {
    const snapshot = React.useSyncExternalStore(
      (onStoreChange: () => void) => {
        mockListeners.add(onStoreChange);
        return () => mockListeners.delete(onStoreChange);
      },
      () => mockSearch,
    );
    // Real Next.js's `useSearchParams()` returns a referentially-stable
    // object across re-renders when the URL hasn't changed — components
    // that `useMemo`/effect-depend on it (e.g. this page's own `filters`)
    // rely on that. Constructing a fresh `URLSearchParams` on every call
    // regardless of whether `snapshot` changed would silently break that
    // contract and cause spurious extra effect re-runs on every unrelated
    // re-render (discovered chasing an apparently-flaky test count).
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
  ListingsApiError: class ListingsApiError extends Error {
    kind: string;
    constructor(message: string, kind = "unknown") {
      super(message);
      this.kind = kind;
    }
  },
  ListingsSyncError: class ListingsSyncError extends Error {
    reason: string | null;
    kind: string;
    constructor(message: string, reason: string | null = null, kind = "unknown") {
      super(message);
      this.reason = reason;
      this.kind = kind;
    }
  },
  fetchAmazonConnection: vi.fn(),
  fetchListingsSummary: vi.fn(),
  fetchListings: vi.fn(),
  fetchListingDetail: vi.fn(),
  triggerListingsSync: vi.fn(),
}));

import { LISTINGS_SYNC_STALE_QUEUE_THRESHOLD_MS, SellerListings } from "@/components/seller-listings";
import {
  fetchAmazonConnection,
  fetchListingDetail,
  fetchListings,
  fetchListingsSummary,
  ListingsApiError,
  ListingsSyncError,
  triggerListingsSync,
} from "@/lib/api";
import type {
  AmazonConnectionOverview,
  ListingCollectionResponse,
  ListingDetail,
  ListingsSummary,
  ListingsSyncEvidence,
} from "@/lib/types";
import { formatProductType } from "@/lib/seller-listings-view";

const US_ID = "11111111-1111-4111-8111-111111111111";
const MX_ID = "22222222-2222-4222-8222-222222222222";

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
    {
      id: MX_ID,
      marketplace_id: "A1AM78C64UM0Y8",
      name: "Amazon.com.mx",
      country_code: "MX",
      domain_name: "www.amazon.com.mx",
      is_participating: true,
      has_suspended_listings: false,
      is_active: true,
      last_seen_at: "2026-08-29T00:00:00.000Z",
    },
  ],
  latest_ingestion: null,
  ads_api: { provider: "ADS_API", status: "NOT_CONNECTED" },
};

function sync(overrides: Partial<ListingsSyncEvidence> = {}): ListingsSyncEvidence {
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
    reported_total_results: null,
    pagination_complete: null,
    last_successful_synchronized_at: null,
    next_retry_at: null,
    ...overrides,
  };
}

function summary(overrides: Partial<ListingsSummary> = {}): ListingsSummary {
  return {
    marketplace_participation_id: US_ID,
    total_listings: 0,
    active_count: 0,
    inactive_count: 0,
    buyable_count: 0,
    not_buyable_count: 0,
    discoverable_count: 0,
    not_discoverable_count: 0,
    with_issues_count: 0,
    without_issues_count: 0,
    issue_severity_error_count: 0,
    issue_severity_warning_count: 0,
    issue_severity_info_count: 0,
    with_asin_count: 0,
    with_consumer_price_count: 0,
    with_fulfillment_availability_count: 0,
    sync: sync(),
    ...overrides,
  };
}

function collection(overrides: Partial<ListingCollectionResponse> = {}): ListingCollectionResponse {
  return { items: [], total: 0, offset: 0, limit: 25, ...overrides };
}

const richSummary = summary({
  total_listings: 10,
  active_count: 9,
  inactive_count: 1,
  buyable_count: 3,
  not_buyable_count: 7,
  discoverable_count: 8,
  not_discoverable_count: 2,
  with_issues_count: 4,
  without_issues_count: 6,
  issue_severity_error_count: 2,
  issue_severity_warning_count: 2,
  issue_severity_info_count: 0,
  with_asin_count: 9,
  with_consumer_price_count: 7,
  with_fulfillment_availability_count: 6,
  sync: sync({
    status: "succeeded",
    started_at: "2026-08-29T08:00:00.000Z",
    completed_at: "2026-08-29T08:05:00.000Z",
    pages_fetched: 1,
    records_received: 10,
    records_accepted: 10,
    records_rejected: 0,
    reported_total_results: 10,
    pagination_complete: true,
    last_successful_synchronized_at: "2026-08-29T08:05:00.000Z",
  }),
});

const richCollection = collection({
  items: [
    {
      id: "aaaaaaaa-0000-4000-8000-000000000001",
      seller_sku: "SYN-SKU-1",
      asin: "B0SYNTH0001",
      product_type: "TOY",
      is_active: true,
      is_buyable: true,
      is_discoverable: true,
      price_amount: "19.99",
      price_currency: "USD",
      issue_count: 0,
      highest_issue_severity: null,
      first_seen_at: "2026-08-01T00:00:00.000Z",
      last_seen_at: "2026-08-29T08:05:00.000Z",
      last_successful_sync_at: "2026-08-29T08:05:00.000Z",
    },
    {
      id: "aaaaaaaa-0000-4000-8000-000000000002",
      seller_sku: "SYN-SKU-2",
      asin: null,
      product_type: null,
      is_active: false,
      is_buyable: false,
      is_discoverable: false,
      price_amount: null,
      price_currency: null,
      issue_count: 2,
      highest_issue_severity: "ERROR",
      first_seen_at: "2026-08-01T00:00:00.000Z",
      last_seen_at: "2026-08-29T08:05:00.000Z",
      last_successful_sync_at: null,
    },
  ],
  total: 2,
});

const richDetail: ListingDetail = {
  id: "aaaaaaaa-0000-4000-8000-000000000002",
  seller_sku: "SYN-SKU-2",
  asin: null,
  item_name: "Synthetic Test Widget",
  product_type: null,
  is_active: false,
  is_buyable: false,
  is_discoverable: false,
  price_amount: null,
  price_currency: null,
  status: ["DISCOVERABLE"],
  offers: [],
  fulfillment_availability: [{ fulfillmentChannelCode: "DEFAULT", quantity: 5 }],
  issues: [
    { code: "INFO_CODE", message: "Informational note", severity: "INFO", categories: ["LISTING"] },
    { code: "ERROR_CODE", message: "Critical problem", severity: "ERROR", categories: ["LISTING"] },
    { code: "WARN_CODE", message: "Needs review", severity: "WARNING", categories: ["LISTING"] },
  ],
  product_types: [{ productType: "TOY", marketplaceId: "ATVPDKIKX0DER" }],
  issue_count: 3,
  highest_issue_severity: "ERROR",
  first_seen_at: "2026-08-01T00:00:00.000Z",
  last_seen_at: "2026-08-29T08:05:00.000Z",
  last_successful_sync_at: null,
};

function setup(initialSearch = "") {
  setMockSearch(initialSearch);
  return render(<SellerListings />);
}

beforeEach(() => {
  vi.clearAllMocks();
  setMockSearch("");
  // jsdom has no real matchMedia implementation; ThemeToggle (rendered by
  // AppShell) needs it. Standard jsdom polyfill, not feature-specific.
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
});

afterEach(() => {
  cleanup();
});

describe("marketplace resolution", () => {
  it("preserves a valid participation already in the URL without probing summaries", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValue(richSummary);
    vi.mocked(fetchListings).mockResolvedValue(richCollection);

    setup(`participation=${MX_ID}`);

    await waitFor(() => expect(screen.getByLabelText(/marketplace/i)).toHaveValue(MX_ID));
    expect(fetchListingsSummary).toHaveBeenCalledTimes(1);
    expect(fetchListingsSummary).toHaveBeenCalledWith(MX_ID);
  });

  it("defaults to the marketplace with a successful sync when the URL has none", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockImplementation(async (id: string) =>
      id === MX_ID ? summary({ marketplace_participation_id: MX_ID, sync: sync({ status: "succeeded", last_successful_synchronized_at: "2026-08-29T01:00:00.000Z" }) }) : summary(),
    );
    vi.mocked(fetchListings).mockResolvedValue(collection());

    setup("");

    await waitFor(() => expect(routerReplace).toHaveBeenCalledWith(expect.stringContaining(`participation=${MX_ID}`), expect.anything()));
  });

  it("falls back to the canonical standard storefront when nothing has synced", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValue(summary());
    vi.mocked(fetchListings).mockResolvedValue(collection());

    setup("");

    await waitFor(() => expect(routerReplace).toHaveBeenCalledWith(expect.stringContaining(`participation=${US_ID}`), expect.anything()));
  });

  it("self-heals an inaccessible/unknown participation id in the URL", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValue(summary());
    vi.mocked(fetchListings).mockResolvedValue(collection());

    setup("participation=99999999-9999-4999-8999-999999999999");

    await waitFor(() => expect(routerReplace).toHaveBeenCalledWith(expect.stringContaining(`participation=${US_ID}`), expect.anything()));
  });

  it("changing marketplace resets pagination and closes open listing detail", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValue(richSummary);
    vi.mocked(fetchListings).mockResolvedValue(richCollection);
    vi.mocked(fetchListingDetail).mockResolvedValue(richDetail);

    setup(`participation=${US_ID}&page=2&listing=${richCollection.items[0].id}`);
    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(/marketplace/i), { target: { value: MX_ID } });

    await waitFor(() => {
      const lastCall = routerReplace.mock.calls.at(-1)?.[0] as string;
      expect(lastCall).toContain(`participation=${MX_ID}`);
      expect(lastCall).not.toContain("page=");
      expect(lastCall).not.toContain("listing=");
    });
  });

  it("never combines data from different marketplaces", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValue(richSummary);
    vi.mocked(fetchListings).mockResolvedValue(richCollection);

    setup(`participation=${US_ID}`);
    await waitFor(() => expect(fetchListings).toHaveBeenCalledWith(US_ID, expect.anything()));
    expect(fetchListings).not.toHaveBeenCalledWith(MX_ID, expect.anything());
  });
});

describe("empty and error states", () => {
  it("shows a message when there is no connected seller account", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue({ ...baseOverview, seller_account_id: null, marketplaces: [] });
    setup();
    await waitFor(() => expect(screen.getByText(/no connected seller account/i)).toBeInTheDocument());
  });

  it("shows a message when there are no marketplaces", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue({ ...baseOverview, marketplaces: [] });
    setup();
    await waitFor(() => expect(screen.getByText(/no marketplaces available/i)).toBeInTheDocument());
  });

  it("shows Not synchronized yet for a marketplace with no Listings run", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValue(summary());
    vi.mocked(fetchListings).mockResolvedValue(collection());
    setup(`participation=${US_ID}`);
    await waitFor(() => expect(screen.getByText(/not synchronized yet/i)).toBeInTheDocument());
  });

  it("shows a zero-listings empty state for a synchronized marketplace with nothing in it", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValue(summary({ sync: sync({ status: "succeeded", last_successful_synchronized_at: "2026-08-29T00:00:00.000Z" }) }));
    vi.mocked(fetchListings).mockResolvedValue(collection());
    setup(`participation=${US_ID}`);
    await waitFor(() => expect(screen.getByText(/no listings yet/i)).toBeInTheDocument());
  });

  it("shows a sanitized message on a listings API failure without raw exception detail", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValue(richSummary);
    vi.mocked(fetchListings).mockRejectedValue(new ListingsApiError("Internal traceback: line 42", "unknown"));
    setup(`participation=${US_ID}`);
    await waitFor(() => expect(screen.getByText(/listings could not be loaded/i)).toBeInTheDocument());
    expect(screen.queryByText(/traceback/i)).not.toBeInTheDocument();
  });

  it("keeps last-known-good listings visible with a restrained warning when the latest sync failed", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValue(
      summary({
        total_listings: 2,
        sync: sync({
          status: "failed",
          failure_class: "malformed_page",
          last_successful_synchronized_at: "2026-08-28T00:00:00.000Z",
        }),
      }),
    );
    vi.mocked(fetchListings).mockResolvedValue(richCollection);
    setup(`participation=${US_ID}`);

    await waitFor(() => expect(screen.getByText("SYN-SKU-1")).toBeInTheDocument());
    expect(screen.getByText(/did not complete/i)).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
  });
});

describe("summary metrics", () => {
  it("renders active, buyable and discoverable as independent counts", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValue(richSummary);
    vi.mocked(fetchListings).mockResolvedValue(richCollection);
    setup(`participation=${US_ID}`);

    await waitFor(() => expect(screen.getByText("Total listings")).toBeInTheDocument());
    expect(screen.getByText("9").closest("div")).toBeTruthy();
    expect(screen.getByText("3").closest("div")).toBeTruthy();
    expect(screen.getByText("8").closest("div")).toBeTruthy();
  });
});

describe("listings table", () => {
  it("renders rows from synthetic fixtures and shows — for missing price/attributes, never 0", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValue(richSummary);
    vi.mocked(fetchListings).mockResolvedValue(richCollection);
    setup(`participation=${US_ID}`);

    await waitFor(() => expect(screen.getByText("SYN-SKU-1")).toBeInTheDocument());
    const row2 = screen.getByText("SYN-SKU-2").closest("tr")!;
    expect(within(row2).getAllByText("—").length).toBeGreaterThan(0);
    expect(within(row2).queryByText("0")).not.toBeInTheDocument();
  });

  it("does not render internal identifiers anywhere in the table", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValue(richSummary);
    vi.mocked(fetchListings).mockResolvedValue(richCollection);
    setup(`participation=${US_ID}`);

    await waitFor(() => expect(screen.getByText("SYN-SKU-1")).toBeInTheDocument());
    expect(screen.queryByText(baseOverview.organization_id)).not.toBeInTheDocument();
    expect(screen.queryByText(baseOverview.seller_account_id!)).not.toBeInTheDocument();
    expect(screen.queryByText(richCollection.items[0].id)).not.toBeInTheDocument();
  });

  it("paginates via the server, requesting the next offset", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValue(richSummary);
    vi.mocked(fetchListings).mockResolvedValue(collection({ items: richCollection.items, total: 60, offset: 0, limit: 25 }));
    setup(`participation=${US_ID}`);

    await waitFor(() => expect(screen.getByText("SYN-SKU-1")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /next/i }));

    await waitFor(() =>
      expect(fetchListings).toHaveBeenLastCalledWith(US_ID, expect.objectContaining({ offset: 25 })),
    );
  });

  it("clicking Details opens the detail drawer for that listing", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValue(richSummary);
    vi.mocked(fetchListings).mockResolvedValue(richCollection);
    vi.mocked(fetchListingDetail).mockResolvedValue(richDetail);
    setup(`participation=${US_ID}`);

    await waitFor(() => expect(screen.getByText("SYN-SKU-1")).toBeInTheDocument());
    const row1 = screen.getByText("SYN-SKU-1").closest("tr")!;
    fireEvent.click(within(row1).getByRole("button", { name: /details/i }));

    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
  });
});

describe("filters, search, and sorting", () => {
  it("debounces the search query before requesting the API", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValue(richSummary);
    vi.mocked(fetchListings).mockResolvedValue(richCollection);
    setup(`participation=${US_ID}`);

    await waitFor(() => expect(screen.getByText("SYN-SKU-1")).toBeInTheDocument());
    const callsBefore = vi.mocked(fetchListings).mock.calls.length;

    fireEvent.change(screen.getByLabelText(/search sku or asin/i), { target: { value: "SYN" } });
    // Immediately after typing, the debounced request must not have fired yet.
    expect(vi.mocked(fetchListings).mock.calls.length).toBe(callsBefore);

    // Real wait comfortably past the 300ms debounce — avoids the known
    // fragility of faking timers alongside React's own scheduler.
    await new Promise((resolve) => setTimeout(resolve, 400));

    await waitFor(() =>
      expect(fetchListings).toHaveBeenLastCalledWith(US_ID, expect.objectContaining({ q: "SYN" })),
    );
  }, 10000);

  it("applies the active filter and resets pagination", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValue(richSummary);
    vi.mocked(fetchListings).mockResolvedValue(richCollection);
    setup(`participation=${US_ID}&page=2`);

    await waitFor(() => expect(screen.getByText("SYN-SKU-1")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText(/^active$/i), { target: { value: "true" } });

    await waitFor(() =>
      expect(fetchListings).toHaveBeenLastCalledWith(US_ID, expect.objectContaining({ isActive: true, offset: 0 })),
    );
  });

  it("applies the has-issues and severity filters", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValue(richSummary);
    vi.mocked(fetchListings).mockResolvedValue(richCollection);
    setup(`participation=${US_ID}`);

    await waitFor(() => expect(screen.getByText("SYN-SKU-1")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText(/^issues$/i), { target: { value: "true" } });
    await waitFor(() => expect(fetchListings).toHaveBeenLastCalledWith(US_ID, expect.objectContaining({ hasIssues: true })));

    fireEvent.change(screen.getByLabelText(/highest severity/i), { target: { value: "ERROR" } });
    await waitFor(() =>
      expect(fetchListings).toHaveBeenLastCalledWith(US_ID, expect.objectContaining({ highestIssueSeverity: "ERROR" })),
    );
  });

  it("applies sorting", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValue(richSummary);
    vi.mocked(fetchListings).mockResolvedValue(richCollection);
    setup(`participation=${US_ID}`);

    await waitFor(() => expect(screen.getByText("SYN-SKU-1")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText(/sort by/i), { target: { value: "price_amount" } });
    await waitFor(() =>
      expect(fetchListings).toHaveBeenLastCalledWith(US_ID, expect.objectContaining({ sortBy: "price_amount" })),
    );
  });

  it("resets all filters via the Reset filters button", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValue(richSummary);
    vi.mocked(fetchListings).mockResolvedValue(richCollection);
    setup(`participation=${US_ID}&active=true&severity=ERROR`);

    await waitFor(() => expect(screen.getByText("SYN-SKU-1")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /reset filters/i }));

    await waitFor(() =>
      expect(fetchListings).toHaveBeenLastCalledWith(
        US_ID,
        expect.objectContaining({ isActive: undefined, highestIssueSeverity: undefined }),
      ),
    );
  });

  it("shows a zero-match empty state distinct from never-synchronized", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValue(richSummary);
    vi.mocked(fetchListings).mockResolvedValue(collection());
    setup(`participation=${US_ID}&q=nomatch`);

    await waitFor(() => expect(screen.getByText(/no listings match these filters/i)).toBeInTheDocument());
  });
});

describe("listing detail", () => {
  it("renders approved fields and orders issues ERROR, WARNING, INFO", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValue(richSummary);
    vi.mocked(fetchListings).mockResolvedValue(richCollection);
    vi.mocked(fetchListingDetail).mockResolvedValue(richDetail);
    setup(`participation=${US_ID}&listing=${richDetail.id}`);

    const dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(within(dialog).getByText("Synthetic Test Widget")).toBeInTheDocument());

    const severityBadges = within(dialog).getAllByText(/^(ERROR|WARNING|INFO)$/);
    expect(severityBadges.map((el) => el.textContent)).toEqual(["ERROR", "WARNING", "INFO"]);
  });

  it("never exposes internal identifiers in the detail panel", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValue(richSummary);
    vi.mocked(fetchListings).mockResolvedValue(richCollection);
    vi.mocked(fetchListingDetail).mockResolvedValue(richDetail);
    setup(`participation=${US_ID}&listing=${richDetail.id}`);

    const dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(within(dialog).getByText("Synthetic Test Widget")).toBeInTheDocument());
    expect(within(dialog).queryByText(baseOverview.organization_id)).not.toBeInTheDocument();
    expect(within(dialog).queryByText(US_ID)).not.toBeInTheDocument();
  });

  it("closing the drawer removes the listing param and can be reopened", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValue(richSummary);
    vi.mocked(fetchListings).mockResolvedValue(richCollection);
    vi.mocked(fetchListingDetail).mockResolvedValue(richDetail);
    setup(`participation=${US_ID}&listing=${richDetail.id}`);

    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /close/i }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(routerReplace.mock.calls.at(-1)?.[0]).not.toContain("listing=");
  });

  it("shows a not-found message for a listing that no longer resolves", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValue(richSummary);
    vi.mocked(fetchListings).mockResolvedValue(richCollection);
    vi.mocked(fetchListingDetail).mockRejectedValue(new ListingsApiError("not found", "not_found"));
    setup(`participation=${US_ID}&listing=${richDetail.id}`);

    await waitFor(() => expect(screen.getByText(/was not found/i)).toBeInTheDocument());
  });
});

describe("navigation", () => {
  it("is reachable via a Seller Data link labeled distinctly from ASIN Analyzer", async () => {
    const { AppShell } = await import("@/components/app-shell");
    render(
      <AppShell current="seller-listings">
        <div>content</div>
      </AppShell>,
    );
    const link = screen.getByRole("link", { name: /seller data/i });
    expect(link).toHaveAttribute("href", "/seller-listings");
    expect(screen.getByRole("link", { name: /^analyze$/i })).toBeInTheDocument();
  });
});

describe("human-readable product types", () => {
  it("formats known SCREAMING_SNAKE_CASE values", () => {
    expect(formatProductType("BLOOD_OXYGEN_MONITOR")).toBe("Blood Oxygen Monitor");
    expect(formatProductType("BODY_STRAP")).toBe("Body Strap");
    expect(formatProductType("HEALTH_PERSONAL_CARE")).toBe("Health Personal Care");
  });

  it("formats a single-word value", () => {
    expect(formatProductType("TOY")).toBe("Toy");
  });

  it("handles empty, missing, and whitespace-only values safely", () => {
    expect(formatProductType(null)).toBe("—");
    expect(formatProductType(undefined)).toBe("—");
    expect(formatProductType("")).toBe("—");
    expect(formatProductType("   ")).toBe("—");
  });

  it("formats a previously unknown value generically, not via a lookup table", () => {
    expect(formatProductType("SOME_BRAND_NEW_CATEGORY_2027")).toBe("Some Brand New Category 2027");
  });

  it("renders formatted product type in the table, not the raw value", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValue(richSummary);
    vi.mocked(fetchListings).mockResolvedValue(
      collection({
        items: [{ ...richCollection.items[0], product_type: "BLOOD_OXYGEN_MONITOR" }],
        total: 1,
      }),
    );
    setup(`participation=${US_ID}`);

    await waitFor(() => expect(screen.getByText("Blood Oxygen Monitor")).toBeInTheDocument());
    expect(screen.queryByText("BLOOD_OXYGEN_MONITOR")).not.toBeInTheDocument();
  });

  it("renders formatted product type in the detail drawer, including the product types collection", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValue(richSummary);
    vi.mocked(fetchListings).mockResolvedValue(richCollection);
    vi.mocked(fetchListingDetail).mockResolvedValue({
      ...richDetail,
      product_type: "BODY_STRAP",
      product_types: [{ productType: "HEALTH_PERSONAL_CARE", marketplaceId: "ATVPDKIKX0DER" }],
    });
    setup(`participation=${US_ID}&listing=${richDetail.id}`);

    const dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(within(dialog).getByText("Body Strap")).toBeInTheDocument());
    expect(within(dialog).getByText("Health Personal Care")).toBeInTheDocument();
    expect(within(dialog).queryByText("BODY_STRAP")).not.toBeInTheDocument();
    expect(within(dialog).queryByText("HEALTH_PERSONAL_CARE")).not.toBeInTheDocument();
  });
});

describe("Sync listings action", () => {
  // The top-level `beforeEach` only calls `vi.clearAllMocks()`, which
  // clears call history but NOT queued `mockResolvedValueOnce` values or
  // a prior test's `mockResolvedValue` default — either can otherwise
  // leak into this describe block's tests, several of which rely on a
  // precise once/default sequence for `fetchListingsSummary` and
  // `triggerListingsSync`. `mockReset()` clears both.
  beforeEach(() => {
    vi.mocked(fetchAmazonConnection).mockReset();
    vi.mocked(fetchListingsSummary).mockReset();
    vi.mocked(fetchListings).mockReset();
    vi.mocked(fetchListingDetail).mockReset();
    vi.mocked(triggerListingsSync).mockReset();
  });

  function job(overrides: Partial<import("@/lib/types").ListingsSyncJobStatus> = {}) {
    return {
      run_id: "aaaaaaaa-1111-4111-8111-000000000099",
      run_type: "listings",
      status: "queued" as const,
      marketplace_participation_id: US_ID,
      pages_fetched: 0,
      records_received: 0,
      records_accepted: 0,
      records_rejected: 0,
      reported_total_results: null,
      pagination_complete: false,
      attempt_count: 0,
      queued_at: "2026-08-29T08:00:00.000Z",
      started_at: null,
      last_heartbeat_at: null,
      next_retry_at: null,
      completed_at: null,
      failure_class: null,
      ...overrides,
    };
  }

  function triggerResponse(
    overrides: Partial<import("@/lib/types").ListingsSyncTriggerResponse> = {},
  ): import("@/lib/types").ListingsSyncTriggerResponse {
    return { reason: "queued", message: null, job: job(), ...overrides };
  }

  // A `queued` summary with a `queued_at` fresh enough to be well under
  // the stale-queue threshold, unless overridden.
  // A `queued` summary meant to be layered on top of `richSummary` (via
  // `{ ...richSummary, sync: queuedSync() }`) — carries over richSummary's
  // own `last_successful_synchronized_at` by default, since a new queued
  // attempt existing must never make a truthful summary "forget" the last
  // successful sync. Every field can still be overridden explicitly.
  function queuedSync(overrides: Partial<import("@/lib/types").ListingsSyncEvidence> = {}) {
    return sync({
      status: "queued",
      queued_at: new Date().toISOString(),
      last_successful_synchronized_at: richSummary.sync.last_successful_synchronized_at,
      ...overrides,
    });
  }

  function noActiveSpinner(): boolean {
    return document.querySelector(".animate-spin") === null;
  }

  it("queues a job, shows the Queued button label, and shows no active-work spinner", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValueOnce(richSummary);
    vi.mocked(fetchListings).mockResolvedValue(richCollection);
    vi.mocked(triggerListingsSync).mockResolvedValue(triggerResponse());
    vi.mocked(fetchListingsSummary).mockResolvedValue({ ...richSummary, sync: queuedSync() });
    setup(`participation=${US_ID}`);

    await waitFor(() => expect(screen.getByText("SYN-SKU-1")).toBeInTheDocument());
    const button = screen.getByRole("button", { name: /^sync listings$/i });
    expect(button).not.toBeDisabled();

    await fireEvent.click(button);

    expect(triggerListingsSync).toHaveBeenCalledWith(US_ID);
    await waitFor(() => expect(screen.getByRole("button", { name: /^queued$/i })).toBeDisabled());
    // The status badge also reads "Queued" — both elements say the same
    // truthful thing, unlike the live defect where the badge said
    // "Queued" but the button simultaneously said "Synchronizing…".
    expect(screen.getAllByText("Queued").length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByRole("button", { name: /synchronizing/i })).not.toBeInTheDocument();
    expect(noActiveSpinner()).toBe(true);
    expect(screen.getByText(/waiting for synchronization worker/i)).toBeInTheDocument();
    expect(screen.getByText(/you may leave this page and return later/i)).toBeInTheDocument();
  });

  it("shows active synchronization (spinner + Synchronizing…) only once a worker has actually claimed the job", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValue(
      summary({ sync: sync({ status: "running", pages_fetched: 3, records_accepted: 12 }) }),
    );
    vi.mocked(fetchListings).mockResolvedValue(richCollection);
    setup(`participation=${US_ID}`);

    await waitFor(() => expect(screen.getByRole("button", { name: /^synchronizing…$/i })).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /^synchronizing…$/i })).toBeDisabled();
    expect(noActiveSpinner()).toBe(false);
  });

  it("shows Waiting to retry (not Synchronizing…, no spinner) while a job is deliberately paused", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValue(
      summary({
        sync: sync({
          status: "waiting_to_retry",
          failure_class: "throttled",
          next_retry_at: new Date(Date.now() + 5 * 60 * 1000).toISOString(),
        }),
      }),
    );
    vi.mocked(fetchListings).mockResolvedValue(richCollection);
    setup(`participation=${US_ID}`);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /^waiting to retry$/i })).toBeInTheDocument(),
    );
    expect(screen.queryByRole("button", { name: /synchronizing/i })).not.toBeInTheDocument();
    expect(noActiveSpinner()).toBe(true);
  });

  it("polls summary while nonterminal and refreshes listings exactly once, only on completion", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValueOnce(richSummary);
    vi.mocked(fetchListings).mockResolvedValue(richCollection);
    vi.mocked(triggerListingsSync).mockResolvedValue(triggerResponse());
    const runningSummary = summary({ sync: sync({ status: "running", pages_fetched: 1 }) });
    const succeededSummary = summary({
      sync: sync({
        status: "succeeded",
        records_accepted: 10,
        completed_at: "2026-08-29T08:05:00.000Z",
        last_successful_synchronized_at: "2026-08-29T08:05:00.000Z",
      }),
    });
    vi.mocked(fetchListingsSummary)
      .mockResolvedValueOnce({ ...richSummary, sync: queuedSync() }) // the immediate post-trigger refetch
      .mockResolvedValueOnce(runningSummary) // first poll tick
      .mockResolvedValue(succeededSummary); // second poll tick onward

    vi.useFakeTimers();
    try {
      setup(`participation=${US_ID}`);
      await vi.waitFor(() => expect(screen.getByText("SYN-SKU-1")).toBeInTheDocument());

      fireEvent.click(screen.getByRole("button", { name: /^sync listings$/i }));
      await vi.waitFor(() => expect(screen.getByRole("button", { name: /^queued$/i })).toBeInTheDocument());
      const listingsCallsBefore = vi.mocked(fetchListings).mock.calls.length;

      await vi.advanceTimersByTimeAsync(3000); // first poll tick: queued -> running
      await vi.waitFor(() =>
        expect(screen.getByRole("button", { name: /^synchronizing…$/i })).toBeInTheDocument(),
      );
      // Still nonterminal: the visible listing data is never cleared or
      // replaced while a job is in progress — no loading/empty state
      // interrupts it, even mid-poll — and no refresh has happened yet.
      expect(screen.getByText("SYN-SKU-1")).toBeInTheDocument();
      expect(vi.mocked(fetchListings).mock.calls.length).toBe(listingsCallsBefore);

      await vi.advanceTimersByTimeAsync(4500); // second poll tick (backoff applied): running -> succeeded
      await vi.waitFor(() => expect(screen.getByText("Completed")).toBeInTheDocument());
      await vi.waitFor(() => expect(vi.mocked(fetchListings).mock.calls.length).toBe(listingsCallsBefore + 1));
      expect(screen.getByRole("button", { name: /^sync listings$/i })).not.toBeDisabled();

      // No polling response ever invokes the trigger endpoint, and only
      // the original click ever did.
      expect(triggerListingsSync).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("backs off the polling interval instead of polling at a constant rate", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValue({ ...richSummary, sync: queuedSync() });
    vi.mocked(fetchListings).mockResolvedValue(richCollection);

    vi.useFakeTimers();
    try {
      setup(`participation=${US_ID}`);
      await vi.waitFor(() => expect(screen.getByRole("button", { name: /^queued$/i })).toBeInTheDocument());
      const callsAtStart = vi.mocked(fetchListingsSummary).mock.calls.length;

      // `vi.waitFor` itself nudges the fake clock forward by small amounts
      // while polling for the condition above to become true, so the
      // *first* scheduled tick's exact remaining delay by this point is
      // only approximately (not exactly) `LISTINGS_SYNC_POLL_INITIAL_MS`.
      // Comfortable margins are used below instead of exact millisecond
      // boundaries, so this test asserts the *shape* of the backoff
      // (first tick well under 3s elapsed has not fired; by ~3.5s it has;
      // a further ~3s — comfortably under the next 4.5s interval — still
      // hasn't fired again; by ~4.5s more it has) without being sensitive
      // to that internal nudging.
      await vi.advanceTimersByTimeAsync(500);
      expect(vi.mocked(fetchListingsSummary).mock.calls.length).toBe(callsAtStart); // well under 3000ms
      await vi.advanceTimersByTimeAsync(3000);
      expect(vi.mocked(fetchListingsSummary).mock.calls.length).toBe(callsAtStart + 1); // first tick has fired

      // Backoff factor is 1.5: the *next* tick is scheduled ~4500ms later,
      // not another ~3000ms — polling at a constant rate would fire here.
      await vi.advanceTimersByTimeAsync(3000);
      expect(vi.mocked(fetchListingsSummary).mock.calls.length).toBe(callsAtStart + 1); // still not due
      await vi.advanceTimersByTimeAsync(2000);
      expect(vi.mocked(fetchListingsSummary).mock.calls.length).toBe(callsAtStart + 2); // second tick has fired
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows the exact throttle message and a human-friendly retry estimate while waiting_to_retry", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValueOnce(richSummary);
    vi.mocked(fetchListings).mockResolvedValue(richCollection);
    vi.mocked(triggerListingsSync).mockResolvedValue(triggerResponse());
    const waitingSummary = summary({
      sync: sync({
        status: "waiting_to_retry",
        failure_class: "throttled",
        next_retry_at: new Date(Date.now() + 5 * 60 * 1000).toISOString(),
      }),
    });
    vi.mocked(fetchListingsSummary).mockResolvedValue(waitingSummary);
    setup(`participation=${US_ID}`);

    await waitFor(() => expect(screen.getByText("SYN-SKU-1")).toBeInTheDocument());
    await fireEvent.click(screen.getByRole("button", { name: /^sync listings$/i }));

    await waitFor(() =>
      expect(
        screen.getByText(/Amazon asked us to slow down\. Synchronization will resume automatically\./),
      ).toBeInTheDocument(),
    );
    expect(screen.getByText(/retrying in about/i)).toBeInTheDocument();
    // Still a normal, usable page — the button stays disabled but nothing crashes.
    expect(screen.getByText("SYN-SKU-1")).toBeInTheDocument();
  });

  it("treats an already-running response as an informational state and attaches to it", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValueOnce(richSummary);
    vi.mocked(fetchListings).mockResolvedValue(richCollection);
    vi.mocked(triggerListingsSync).mockResolvedValue(
      triggerResponse({
        reason: "already_running",
        message: "A Listings synchronization is already running for this marketplace.",
        job: job({ status: "started" }),
      }),
    );
    vi.mocked(fetchListingsSummary).mockResolvedValue(summary({ sync: sync({ status: "running" }) }));
    setup(`participation=${US_ID}`);

    await waitFor(() => expect(screen.getByText("SYN-SKU-1")).toBeInTheDocument());
    await fireEvent.click(screen.getByRole("button", { name: /^sync listings$/i }));

    await waitFor(() => expect(screen.getByText(/already running/i)).toBeInTheDocument());
    expect(screen.getByText("SYN-SKU-1")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: /^synchronizing…$/i })).toBeDisabled());
  });

  it.each([
    ["cooldown", "Please wait a moment before synchronizing this marketplace again."],
    [
      "queue_backlog_limit_reached",
      "Too many Listings synchronizations are already queued for this account. Try again shortly.",
    ],
    ["scope_inactive", "This marketplace or seller account is not active."],
  ])("shows a sanitized message for reason=%s, never raw backend text", async (reason, message) => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValueOnce(richSummary);
    vi.mocked(fetchListings).mockResolvedValue(richCollection);
    vi.mocked(triggerListingsSync).mockResolvedValue(
      triggerResponse({ reason: reason as never, message, job: null }),
    );
    vi.mocked(fetchListingsSummary).mockResolvedValue(richSummary);
    setup(`participation=${US_ID}`);

    await waitFor(() => expect(screen.getByText("SYN-SKU-1")).toBeInTheDocument());
    await fireEvent.click(screen.getByRole("button", { name: /^sync listings$/i }));

    await waitFor(() => expect(screen.getByText(message)).toBeInTheDocument());
    expect(screen.queryByText(/traceback|exception|stack/i)).not.toBeInTheDocument();
  });

  it("prevents a second click while a trigger request is already in flight (double-click prevention)", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValue(richSummary);
    vi.mocked(fetchListings).mockResolvedValue(richCollection);
    let resolveTrigger!: (value: ReturnType<typeof triggerResponse>) => void;
    vi.mocked(triggerListingsSync).mockReturnValue(
      new Promise((resolve) => {
        resolveTrigger = resolve;
      }),
    );
    setup(`participation=${US_ID}`);

    await waitFor(() => expect(screen.getByText("SYN-SKU-1")).toBeInTheDocument());
    const button = screen.getByRole("button", { name: /^sync listings$/i });
    await fireEvent.click(button);
    await fireEvent.click(screen.getByRole("button", { name: /^sync listings$/i }));
    await fireEvent.click(screen.getByRole("button", { name: /^sync listings$/i }));

    expect(triggerListingsSync).toHaveBeenCalledTimes(1);
    resolveTrigger(triggerResponse());
  });

  it("shows the retry_allowed_at time in the cooldown message when provided", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValueOnce(richSummary);
    vi.mocked(fetchListings).mockResolvedValue(richCollection);
    vi.mocked(triggerListingsSync).mockResolvedValue(
      triggerResponse({
        reason: "cooldown",
        message: "Please wait a moment before synchronizing this marketplace again.",
        job: job({ status: "succeeded" }),
        retry_allowed_at: "2026-09-01T13:35:00.000Z",
      }),
    );
    vi.mocked(fetchListingsSummary).mockResolvedValue(richSummary);
    setup(`participation=${US_ID}`);

    await waitFor(() => expect(screen.getByText("SYN-SKU-1")).toBeInTheDocument());
    await fireEvent.click(screen.getByRole("button", { name: /^sync listings$/i }));

    await waitFor(() => expect(screen.getByText(/recently synchronized/i)).toBeInTheDocument());
    expect(screen.getByText(/you can synchronize again at/i)).toBeInTheDocument();
    // Never the raw backend sentence when a structured retry time exists.
    expect(
      screen.queryByText("Please wait a moment before synchronizing this marketplace again."),
    ).not.toBeInTheDocument();
  });

  it("falls back to the plain cooldown message when retry_allowed_at is absent", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValueOnce(richSummary);
    vi.mocked(fetchListings).mockResolvedValue(richCollection);
    vi.mocked(triggerListingsSync).mockResolvedValue(
      triggerResponse({
        reason: "cooldown",
        message: "Please wait a moment before synchronizing this marketplace again.",
        job: job({ status: "succeeded" }),
        retry_allowed_at: null,
      }),
    );
    vi.mocked(fetchListingsSummary).mockResolvedValue(richSummary);
    setup(`participation=${US_ID}`);

    await waitFor(() => expect(screen.getByText("SYN-SKU-1")).toBeInTheDocument());
    await fireEvent.click(screen.getByRole("button", { name: /^sync listings$/i }));

    await waitFor(() =>
      expect(
        screen.getByText("Please wait a moment before synchronizing this marketplace again."),
      ).toBeInTheDocument(),
    );
  });

  it("ignores a trigger response for a marketplace the user has since switched away from", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockImplementation(async (id: string) =>
      id === US_ID
        ? { ...richSummary, marketplace_participation_id: US_ID }
        : summary({ marketplace_participation_id: MX_ID, sync: sync({ status: "never_synchronized" }) }),
    );
    vi.mocked(fetchListings).mockImplementation(async (id: string) =>
      id === US_ID ? richCollection : collection(),
    );
    let resolveTrigger!: (value: ReturnType<typeof triggerResponse>) => void;
    vi.mocked(triggerListingsSync).mockReturnValue(
      new Promise((resolve) => {
        resolveTrigger = resolve;
      }),
    );
    setup(`participation=${US_ID}`);

    await waitFor(() => expect(screen.getByText("SYN-SKU-1")).toBeInTheDocument());
    await fireEvent.click(screen.getByRole("button", { name: /^sync listings$/i }));
    expect(triggerListingsSync).toHaveBeenCalledWith(US_ID);

    // Switch marketplaces while the US trigger POST is still unresolved.
    fireEvent.change(screen.getByLabelText(/marketplace/i), { target: { value: MX_ID } });
    await waitFor(() => expect(screen.getByLabelText(/marketplace/i)).toHaveValue(MX_ID));

    // Now the stale US response arrives.
    resolveTrigger(triggerResponse({ job: job({ status: "queued", marketplace_participation_id: US_ID }) }));

    // The now-displayed MX marketplace must never show US's "Queued"
    // evidence, an info/success message meant for US, or have its
    // pagination reset by a request it never made.
    await waitFor(() => expect(screen.queryByText(/synchronizing…|^queued$/i)).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: /^sync listings$/i })).not.toBeDisabled();
  });

  it("is scoped to the currently selected marketplace, not another one", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValue(richSummary);
    vi.mocked(fetchListings).mockResolvedValue(richCollection);
    vi.mocked(triggerListingsSync).mockResolvedValue(triggerResponse());
    setup(`participation=${MX_ID}`);

    await waitFor(() => expect(screen.getByText("SYN-SKU-1")).toBeInTheDocument());
    await fireEvent.click(screen.getByRole("button", { name: /^sync listings$/i }));

    await waitFor(() => expect(triggerListingsSync).toHaveBeenCalledWith(MX_ID));
    expect(triggerListingsSync).not.toHaveBeenCalledWith(US_ID);
  });

  it("is disabled while no marketplace is selected", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValue(summary());
    vi.mocked(fetchListings).mockResolvedValue(collection());
    setup("");

    await waitFor(() => expect(screen.getByText(/choosing a marketplace/i)).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /^sync listings$/i })).not.toBeInTheDocument();
  });

  it("resets pagination as soon as a job is queued, without waiting for completion", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValueOnce(richSummary);
    vi.mocked(fetchListings).mockResolvedValue(
      collection({ items: richCollection.items, total: 60, offset: 25, limit: 25 }),
    );
    vi.mocked(triggerListingsSync).mockResolvedValue(triggerResponse());
    vi.mocked(fetchListingsSummary).mockResolvedValue({ ...richSummary, sync: queuedSync() });
    setup(`participation=${US_ID}&page=2`);

    await waitFor(() => expect(screen.getByText("SYN-SKU-1")).toBeInTheDocument());
    await fireEvent.click(screen.getByRole("button", { name: /^sync listings$/i }));

    await waitFor(() =>
      expect(fetchListings).toHaveBeenLastCalledWith(US_ID, expect.objectContaining({ offset: 0 })),
    );
  });

  it("preserves the selected marketplace after a sync completes", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValueOnce(richSummary);
    vi.mocked(fetchListings).mockResolvedValue(richCollection);
    vi.mocked(triggerListingsSync).mockResolvedValue(triggerResponse());
    vi.mocked(fetchListingsSummary).mockResolvedValue(
      summary({ sync: sync({ status: "succeeded", records_accepted: 10 }) }),
    );
    setup(`participation=${US_ID}`);

    await waitFor(() => expect(screen.getByText("SYN-SKU-1")).toBeInTheDocument());
    await fireEvent.click(screen.getByRole("button", { name: /^sync listings$/i }));

    await waitFor(() => expect(screen.getByText("Completed")).toBeInTheDocument());
    expect(screen.getByLabelText(/marketplace/i)).toHaveValue(US_ID);
  });

  it("discovers and resumes an already-running job on page load without any user action", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValue(summary({ sync: sync({ status: "running", pages_fetched: 2 }) }));
    vi.mocked(fetchListings).mockResolvedValue(richCollection);
    setup(`participation=${US_ID}`);

    await waitFor(() => expect(screen.getByRole("button", { name: /^synchronizing…$/i })).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /^synchronizing…$/i })).toBeDisabled();
    // No click occurred — this state was discovered purely from the
    // summary the page loaded on mount.
    expect(triggerListingsSync).not.toHaveBeenCalled();
  });

  it("discovers and resumes an already-queued job on page load, truthfully (not as Synchronizing…)", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValue({ ...richSummary, sync: queuedSync() });
    vi.mocked(fetchListings).mockResolvedValue(richCollection);
    setup(`participation=${US_ID}`);

    await waitFor(() => expect(screen.getByRole("button", { name: /^queued$/i })).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /synchronizing/i })).not.toBeInTheDocument();
    expect(noActiveSpinner()).toBe(true);
    expect(triggerListingsSync).not.toHaveBeenCalled();
    // Existing successful-sync evidence and listing data remain visible —
    // the newly-discovered queued job does not hide or override them.
    expect(screen.getByText(/last successful sync:\s*29 aug 2026/i)).toBeInTheDocument();
    expect(screen.getByText("SYN-SKU-1")).toBeInTheDocument();
  });

  it("stops polling once the component unmounts, including an in-flight response", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    let resolveInFlight!: (value: import("@/lib/types").ListingsSummary) => void;
    vi.mocked(fetchListingsSummary)
      .mockResolvedValueOnce(summary({ sync: sync({ status: "running" }) }))
      .mockReturnValueOnce(
        new Promise((resolve) => {
          resolveInFlight = resolve;
        }),
      )
      .mockResolvedValue(summary({ sync: sync({ status: "running" }) }));
    vi.mocked(fetchListings).mockResolvedValue(richCollection);

    // Fake timers from the very start — a `setTimeout` this component
    // schedules under real timers would be orphaned the moment fake
    // timers are switched on mid-test (real timers never learn a fake
    // clock is now advancing, and vice versa), which is exactly why the
    // in-flight poll below would otherwise never actually fire.
    vi.useFakeTimers();
    try {
      const { unmount } = setup(`participation=${US_ID}`);
      await vi.waitFor(() => expect(screen.getByRole("button", { name: /^synchronizing…$/i })).toBeInTheDocument());
      const callsBeforeUnmount = vi.mocked(fetchListingsSummary).mock.calls.length;

      await vi.advanceTimersByTimeAsync(3500); // fires the scheduled tick, leaving its request in flight
      expect(vi.mocked(fetchListingsSummary).mock.calls.length).toBe(callsBeforeUnmount + 1);
      unmount();
      // Resolve the in-flight request only *after* unmount — the
      // `cancelled` guard inside the polling effect must prevent this
      // from updating state or scheduling anything further.
      resolveInFlight(summary({ sync: sync({ status: "running" }) }));
      await vi.advanceTimersByTimeAsync(30000);
      expect(vi.mocked(fetchListingsSummary).mock.calls.length).toBe(callsBeforeUnmount + 1); // no more after unmount
    } finally {
      vi.useRealTimers();
    }
  });

  it("marketplace change cancels polling for the previous marketplace", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockImplementation(async (id: string) =>
      id === US_ID
        ? summary({ marketplace_participation_id: US_ID, sync: sync({ status: "running" }) })
        : summary({ marketplace_participation_id: MX_ID, sync: sync({ status: "never_synchronized" }) }),
    );
    vi.mocked(fetchListings).mockResolvedValue(richCollection);
    setup(`participation=${US_ID}`);

    await waitFor(() => expect(screen.getByRole("button", { name: /^synchronizing…$/i })).toBeInTheDocument());
    const callsForUsBeforeSwitch = vi.mocked(fetchListingsSummary).mock.calls.filter((c) => c[0] === US_ID).length;

    fireEvent.change(screen.getByLabelText(/marketplace/i), { target: { value: MX_ID } });
    await waitFor(() => expect(screen.getByLabelText(/marketplace/i)).toHaveValue(MX_ID));
    await waitFor(() => expect(screen.getByRole("button", { name: /^sync listings$/i })).toBeInTheDocument());

    vi.useFakeTimers();
    try {
      await vi.advanceTimersByTimeAsync(10000);
    } finally {
      vi.useRealTimers();
    }
    // No further polling for the now-abandoned US participation.
    const callsForUsAfterSwitch = vi.mocked(fetchListingsSummary).mock.calls.filter((c) => c[0] === US_ID).length;
    expect(callsForUsAfterSwitch).toBe(callsForUsBeforeSwitch);
  });

  it("pauses polling while the tab is hidden and does an immediate catch-up fetch when visible again", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValue({ ...richSummary, sync: queuedSync() });
    vi.mocked(fetchListings).mockResolvedValue(richCollection);

    vi.useFakeTimers();
    try {
      setup(`participation=${US_ID}`);
      await vi.waitFor(() => expect(screen.getByRole("button", { name: /^queued$/i })).toBeInTheDocument());
      const callsBeforeHidden = vi.mocked(fetchListingsSummary).mock.calls.length;

      Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
      document.dispatchEvent(new Event("visibilitychange"));
      await vi.advanceTimersByTimeAsync(15000); // well past several normal poll intervals
      // No fetch while hidden.
      expect(vi.mocked(fetchListingsSummary).mock.calls.length).toBe(callsBeforeHidden);

      Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
      document.dispatchEvent(new Event("visibilitychange"));
      await vi.advanceTimersByTimeAsync(0);
      // Immediate catch-up fetch, without waiting for the next scheduled tick.
      expect(vi.mocked(fetchListingsSummary).mock.calls.length).toBe(callsBeforeHidden + 1);
    } finally {
      vi.useRealTimers();
      Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
    }
  });

  it("stops automatic polling and shows Still queued after the no-progress threshold, without ever creating another job", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    const staleQueuedAt = new Date(Date.now() - LISTINGS_SYNC_STALE_QUEUE_THRESHOLD_MS - 1000).toISOString();
    vi.mocked(fetchListingsSummary).mockResolvedValue({ ...richSummary, sync: queuedSync({ queued_at: staleQueuedAt }) });
    vi.mocked(fetchListings).mockResolvedValue(richCollection);

    vi.useFakeTimers();
    try {
      setup(`participation=${US_ID}`);
      await vi.waitFor(() => expect(screen.getByText(/still queued/i)).toBeInTheDocument());
      expect(screen.getByText(/processing has not started yet/i)).toBeInTheDocument();
      // 12B.3H: honest about the actual limits of what this page can know —
      // never implies a worker is confirmed to be running when the only
      // real signal available is the job's own recorded (queued) status.
      expect(screen.getByText(/no way to confirm whether a synchronization worker is currently running/i)).toBeInTheDocument();
      // Existing data remains visible and truthful throughout.
      expect(screen.getByText("SYN-SKU-1")).toBeInTheDocument();
      expect(screen.getByText(/last successful sync:\s*29 aug 2026/i)).toBeInTheDocument();

      const callsAtSuspension = vi.mocked(fetchListingsSummary).mock.calls.length;
      await vi.advanceTimersByTimeAsync(60000);
      // No further automatic polling once suspended.
      expect(vi.mocked(fetchListingsSummary).mock.calls.length).toBe(callsAtSuspension);
      expect(triggerListingsSync).not.toHaveBeenCalled(); // never auto-creates another job

      fireEvent.click(screen.getByRole("button", { name: /refresh status/i }));
      await vi.waitFor(() =>
        expect(vi.mocked(fetchListingsSummary).mock.calls.length).toBe(callsAtSuspension + 1),
      );
      expect(triggerListingsSync).not.toHaveBeenCalled(); // manual refresh never triggers a new job either
    } finally {
      vi.useRealTimers();
    }
  });

  it("resumes normal polling if a manual refresh reveals the job is no longer merely queued", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    const staleQueuedAt = new Date(Date.now() - LISTINGS_SYNC_STALE_QUEUE_THRESHOLD_MS - 1000).toISOString();
    vi.mocked(fetchListingsSummary)
      .mockResolvedValueOnce({ ...richSummary, sync: queuedSync({ queued_at: staleQueuedAt }) })
      .mockResolvedValue(summary({ sync: sync({ status: "running", pages_fetched: 1 }) }));
    vi.mocked(fetchListings).mockResolvedValue(richCollection);

    vi.useFakeTimers();
    try {
      setup(`participation=${US_ID}`);
      await vi.waitFor(() => expect(screen.getByText(/still queued/i)).toBeInTheDocument());

      fireEvent.click(screen.getByRole("button", { name: /refresh status/i }));
      await vi.waitFor(() =>
        expect(screen.getByRole("button", { name: /^synchronizing…$/i })).toBeInTheDocument(),
      );
      expect(screen.queryByText(/still queued/i)).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("renders the sync button accessibly at a narrow (390px) viewport width", async () => {
    Object.defineProperty(window, "innerWidth", { writable: true, configurable: true, value: 390 });
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListingsSummary).mockResolvedValue(richSummary);
    vi.mocked(fetchListings).mockResolvedValue(richCollection);
    setup(`participation=${US_ID}`);

    await waitFor(() => expect(screen.getByText("SYN-SKU-1")).toBeInTheDocument());
    const button = screen.getByRole("button", { name: /^sync listings$/i });
    expect(button).toBeVisible();
    expect(button.tagName).toBe("BUTTON");
  });

  // --- reproduces the exact live-observed defect (2026-08-29) ------------

  it("regression: a queued job with no lease/heartbeat never renders as active work, and prior evidence stays visible", async () => {
    // 1. Existing successful Listings run and persisted listings.
    vi.mocked(fetchAmazonConnection).mockResolvedValue(baseOverview);
    vi.mocked(fetchListings).mockResolvedValue(richCollection);
    // 2. New queued run: nullable started_at, no lease, no heartbeat — the
    // exact shape `AmazonIngestionRun` has immediately after `enqueue_
    // listings_run`, before any worker exists to claim it.
    // 3. Summary returns the queued run as current synchronization evidence.
    vi.mocked(fetchListingsSummary).mockResolvedValue({
      ...richSummary,
      sync: sync({
        status: "queued",
        queued_at: new Date().toISOString(),
        started_at: null,
        pages_fetched: null,
        records_received: null,
        records_accepted: null,
        // The prior completed run's timestamp, explicitly carried over —
        // a truthful summary never forgets the last successful sync just
        // because a new (queued, not-yet-run) attempt now exists.
        last_successful_synchronized_at: richSummary.sync.last_successful_synchronized_at,
      }),
    });
    setup(`participation=${US_ID}`);

    // 4. UI displays truthful queued/waiting state.
    await waitFor(() => expect(screen.getByRole("button", { name: /^queued$/i })).toBeInTheDocument());
    expect(screen.getByText(/waiting for synchronization worker/i)).toBeInTheDocument();
    // 6. No indefinite active-work presentation occurs.
    expect(screen.queryByRole("button", { name: /synchronizing/i })).not.toBeInTheDocument();
    expect(noActiveSpinner()).toBe(true);
    // 5. Previous successful-sync timestamp and existing listings remain visible.
    expect(screen.getByText(/last successful sync:\s*29 aug 2026/i)).toBeInTheDocument();
    expect(screen.getByText("SYN-SKU-1")).toBeInTheDocument();
    // Never fabricated from browser state alone.
    expect(triggerListingsSync).not.toHaveBeenCalled();
  });
});
