import * as React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// 12C — Amazon Ads read-only foundation. Sanitized fixtures only, never
// AJ Duran's real advertising data (see the governing task's explicit
// "do not present fixture data as real" requirement).

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
  usePathname: () => "/seller/advertising",
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
  AdsApiError: class AdsApiError extends Error {
    kind: string;
    constructor(message: string, kind = "unknown") {
      super(message);
      this.kind = kind;
    }
  },
  fetchAdsConnectionStatus: vi.fn(),
  fetchAdsProfiles: vi.fn(),
  selectAdsProfile: vi.fn(),
  fetchAdsSyncStatus: vi.fn(),
  fetchAdsOverview: vi.fn(),
  fetchAdsPerformanceSeries: vi.fn(),
  fetchAdsCampaigns: vi.fn(),
}));

import { SellerAdvertising } from "@/components/seller-advertising";
import {
  AdsApiError,
  fetchAdsCampaigns,
  fetchAdsConnectionStatus,
  fetchAdsOverview,
  fetchAdsPerformanceSeries,
  fetchAdsProfiles,
  fetchAdsSyncStatus,
} from "@/lib/api";

const SANITIZED_PROFILE = {
  id: "profile-row-1",
  profile_id: "999000111",
  account_id: "ENTITY-SAMPLE",
  account_type: "seller",
  marketplace_country_code: "US",
  currency_code: "USD",
  timezone: "America/Los_Angeles",
  region: "NA" as const,
  display_name: "Sample Advertiser",
  is_selected: true,
  sync_state: "synced" as const,
};

const CONNECTED_STATUS = {
  status: "connected" as const,
  persisted: true,
  organization_id: "org-1",
  configured: true,
  authorized_at: "2026-09-01T00:00:00Z",
  profiles: [SANITIZED_PROFILE],
  selected_profile_id: SANITIZED_PROFILE.id,
};

beforeEach(() => {
  vi.clearAllMocks();
  setMockSearch("");
});

afterEach(() => {
  cleanup();
});

describe("SellerAdvertising — connection states", () => {
  it("shows a not-connected message when Ads is not configured", async () => {
    vi.mocked(fetchAdsConnectionStatus).mockResolvedValue({
      status: "not_connected",
      persisted: true,
      organization_id: "org-1",
      configured: false,
      authorized_at: null,
      profiles: [],
      selected_profile_id: null,
    });
    vi.mocked(fetchAdsProfiles).mockResolvedValue([]);

    render(<SellerAdvertising />);

    await waitFor(() => expect(screen.getByText("Amazon Ads is not connected yet")).toBeInTheDocument());
  });

  it("shows a retry action when the connection status request fails", async () => {
    vi.mocked(fetchAdsConnectionStatus).mockRejectedValue(new AdsApiError("Server is unreachable.", "unavailable"));
    vi.mocked(fetchAdsProfiles).mockRejectedValue(new AdsApiError("Server is unreachable.", "unavailable"));

    render(<SellerAdvertising />);

    await waitFor(() => expect(screen.getByText("Server is unreachable.")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("prompts profile selection when connected but no profile is selected", async () => {
    vi.mocked(fetchAdsConnectionStatus).mockResolvedValue({
      ...CONNECTED_STATUS,
      selected_profile_id: null,
      profiles: [{ ...SANITIZED_PROFILE, is_selected: false }],
    });
    vi.mocked(fetchAdsProfiles).mockResolvedValue([{ ...SANITIZED_PROFILE, is_selected: false }]);

    render(<SellerAdvertising />);

    await waitFor(() =>
      expect(screen.getByText("Select the advertiser profile to synchronize")).toBeInTheDocument(),
    );
    expect(screen.getByText("Sample Advertiser")).toBeInTheDocument();
  });

  it("renders populated metrics and campaigns once connected with a selected profile", async () => {
    vi.mocked(fetchAdsConnectionStatus).mockResolvedValue(CONNECTED_STATUS);
    vi.mocked(fetchAdsProfiles).mockResolvedValue([SANITIZED_PROFILE]);
    vi.mocked(fetchAdsSyncStatus).mockResolvedValue({
      status: "synced",
      last_synced_at: "2026-09-11T00:00:00Z",
      synced_through_date: "2026-09-11",
      last_report_status: "succeeded",
    });
    vi.mocked(fetchAdsOverview).mockResolvedValue({
      start_date: "2026-08-12",
      end_date: "2026-09-11",
      currency_code: "USD",
      spend: "123.45",
      attributed_sales: "678.90",
      impressions: 10000,
      clicks: 250,
      attributed_orders: 12,
      acos: "18.18",
      roas: "5.50",
      ctr: "2.50",
      cpc: "0.49",
    });
    vi.mocked(fetchAdsPerformanceSeries).mockResolvedValue({
      points: [
        { date: "2026-09-10", spend: "10.00", attributed_sales: "50.00", impressions: 100, clicks: 5, attributed_orders: 1 },
      ],
    });
    vi.mocked(fetchAdsCampaigns).mockResolvedValue({
      items: [
        {
          id: "campaign-1",
          external_campaign_id: "c-sample-1",
          name: "Sample Campaign",
          state: "ENABLED",
          targeting_type: "manual",
          daily_budget: "25.00",
          currency_code: "USD",
          start_date: null,
          end_date: null,
        },
      ],
      page: { offset: 0, limit: 25, total: 1 },
    });

    render(<SellerAdvertising />);

    await waitFor(() => expect(screen.getByText("Sample Campaign")).toBeInTheDocument());
    expect(screen.getByText("$123.45")).toBeInTheDocument();
    expect(screen.getByText("18.18%")).toBeInTheDocument();
  });

  it("shows an empty state when no campaigns have synchronized yet", async () => {
    vi.mocked(fetchAdsConnectionStatus).mockResolvedValue(CONNECTED_STATUS);
    vi.mocked(fetchAdsProfiles).mockResolvedValue([SANITIZED_PROFILE]);
    vi.mocked(fetchAdsSyncStatus).mockResolvedValue({
      status: "awaiting_first_sync",
      last_synced_at: null,
      synced_through_date: null,
      last_report_status: null,
    });
    vi.mocked(fetchAdsOverview).mockResolvedValue({
      start_date: "2026-08-12",
      end_date: "2026-09-11",
      currency_code: "USD",
      spend: "0",
      attributed_sales: "0",
      impressions: 0,
      clicks: 0,
      attributed_orders: 0,
      acos: null,
      roas: null,
      ctr: null,
      cpc: null,
    });
    vi.mocked(fetchAdsPerformanceSeries).mockResolvedValue({ points: [] });
    vi.mocked(fetchAdsCampaigns).mockResolvedValue({ items: [], page: { offset: 0, limit: 25, total: 0 } });

    render(<SellerAdvertising />);

    await waitFor(() => expect(screen.getByText("No campaigns have synchronized yet.")).toBeInTheDocument());
    expect(screen.getByText("Waiting for first sync")).toBeInTheDocument();
  });
});
