import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
  }: {
    href: string;
    children: React.ReactNode;
  }) => <a href={href}>{children}</a>,
}));

vi.mock("@/lib/api", () => ({
  AmazonConnectionError: class AmazonConnectionError extends Error {
    kind: "unavailable" | "unknown";
    constructor(message: string, kind: "unavailable" | "unknown" = "unknown") {
      super(message);
      this.name = "AmazonConnectionError";
      this.kind = kind;
    }
  },
  fetchAmazonConnection: vi.fn(),
  testAmazonConnection: vi.fn(),
  authorizeAmazonConnection: vi.fn(),
}));

import { AmazonConnection } from "@/components/amazon-connection";
import {
  AmazonConnectionError,
  authorizeAmazonConnection,
  fetchAmazonConnection,
  testAmazonConnection,
} from "@/lib/api";
import type {
  AmazonAuthorizationStart,
  AmazonConnectionOverview,
  AmazonConnectionTestResult,
  AmazonSellerMarketplace,
} from "@/lib/types";

const fallbackOverview: AmazonConnectionOverview = {
  status: "NOT_CONNECTED",
  connection_status: "not_connected",
  persisted: false,
  provider: "SP_API",
  environment: "PRODUCTION",
  region: "na",
  marketplace: "amazon.com",
  application: "EWise",
  credentials_configured: true,
  selling_partner_id: null,
  authorized_at: null,
  last_successful_validation_at: null,
  last_successful_sync_at: null,
  last_error_code: null,
  last_test_at: null,
  organization_id: "11111111-1111-4111-8111-111111111111",
  seller_account_id: null,
  seller_account_display_name: null,
  marketplaces: [],
  latest_ingestion: null,
  ads_api: { provider: "ADS_API", status: "NOT_CONNECTED" },
};

const persistedOverview: AmazonConnectionOverview = {
  ...fallbackOverview,
  persisted: true,
  selling_partner_id: "A1SELLERID",
  last_successful_validation_at: "2026-08-23T03:00:00.000Z",
};

const pendingOverview: AmazonConnectionOverview = {
  ...fallbackOverview,
  persisted: true,
  connection_status: "pending_authorization",
};

const pendingValidationOverview: AmazonConnectionOverview = {
  ...fallbackOverview,
  persisted: true,
  connection_status: "pending_validation",
  authorized_at: "2026-08-25T05:14:00.000Z",
};

const secretLostOverview: AmazonConnectionOverview = {
  ...pendingValidationOverview,
  last_error_code: "secret_access_failed",
};

// Connected but never synchronized — 12B.2B "never-synchronized" state.
const connectedOverview: AmazonConnectionOverview = {
  ...pendingValidationOverview,
  connection_status: "connected",
  selling_partner_id: "A1SELLERID",
  last_successful_validation_at: "2026-08-25T05:20:00.000Z",
  last_error_code: null,
};

const usMarketplace: AmazonSellerMarketplace = {
  marketplace_id: "ATVPDKIKX0DER",
  name: "Amazon.com",
  country_code: "US",
  domain_name: "www.amazon.com",
  is_participating: true,
  has_suspended_listings: false,
  is_active: true,
  last_seen_at: "2026-08-25T06:00:00.000Z",
};

// Connected and synchronized — the precise-wording state.
const connectedSyncedOverview: AmazonConnectionOverview = {
  ...connectedOverview,
  seller_account_id: "22222222-2222-4222-8222-222222222222",
  seller_account_display_name: "BestSellerStore",
  last_successful_sync_at: "2026-08-25T06:00:00.000Z",
  marketplaces: [usMarketplace],
  latest_ingestion: {
    status: "succeeded",
    started_at: "2026-08-25T05:59:00.000Z",
    completed_at: "2026-08-25T06:00:00.000Z",
    records_accepted: 1,
    failure_class: null,
  },
};

// A marketplace Amazon stopped reporting on the latest snapshot — preserved, marked inactive.
const staleMarketplaceOverview: AmazonConnectionOverview = {
  ...connectedSyncedOverview,
  marketplaces: [
    usMarketplace,
    {
      marketplace_id: "A2EUQ1WTGCTBG2",
      name: "Amazon.ca",
      country_code: "CA",
      domain_name: "www.amazon.ca",
      is_participating: true,
      has_suspended_listings: false,
      is_active: false,
      last_seen_at: "2026-08-20T06:00:00.000Z",
    },
  ],
};

const nonParticipatingAndSuspendedOverview: AmazonConnectionOverview = {
  ...connectedSyncedOverview,
  marketplaces: [
    {
      ...usMarketplace,
      marketplace_id: "A2NOTPARTICIPATING",
      is_participating: false,
    },
    {
      ...usMarketplace,
      marketplace_id: "A2SUSPENDEDLISTINGS",
      has_suspended_listings: true,
    },
  ],
};

// A prior successful sync populated marketplaces; the latest run then failed —
// the failure must not wipe out or hide the previously synchronized data.
const failedSyncOverview: AmazonConnectionOverview = {
  ...connectedSyncedOverview,
  latest_ingestion: {
    status: "failed",
    started_at: "2026-08-25T07:00:00.000Z",
    completed_at: "2026-08-25T07:00:05.000Z",
    records_accepted: 0,
    failure_class: "database_failure",
  },
};

// A prior successful sync, and a new attempt currently running — prior data
// must be retained and labeled as last-known, not as the current result.
const runningSyncOverview: AmazonConnectionOverview = {
  ...connectedSyncedOverview,
  latest_ingestion: {
    status: "started",
    started_at: "2026-08-25T08:00:00.000Z",
    completed_at: null,
    records_accepted: 0,
    failure_class: null,
  },
};

// Marketplace rows are present (e.g. a legacy/inconsistent record) but
// `last_successful_sync_at` was never set — presence alone must never claim
// synchronization succeeded.
const marketplacesWithoutSuccessTimestampOverview: AmazonConnectionOverview = {
  ...connectedOverview,
  marketplaces: [usMarketplace],
  last_successful_sync_at: null,
  latest_ingestion: null,
};

const ownershipConflictOverview: AmazonConnectionOverview = {
  ...connectedOverview,
  connection_status: "error",
  last_error_code: "ownership_conflict",
};

const reauthRequiredOverview: AmazonConnectionOverview = {
  ...connectedOverview,
  connection_status: "error",
  last_error_code: "requires_reauth",
};

const degradedOverview: AmazonConnectionOverview = {
  ...connectedOverview,
  connection_status: "degraded",
  last_error_code: "sp_api_unavailable",
};

// 12B.2B live-sync remediation: the stored, OAuth-captured identity was
// absent (or a defensive equality check disagreed with it) — must render as
// needing attention/reauthorization, never as a healthy connection.
const identityMissingOverview: AmazonConnectionOverview = {
  ...connectedOverview,
  connection_status: "error",
  last_error_code: "identity_missing",
};

const identityConflictOverview: AmazonConnectionOverview = {
  ...connectedOverview,
  connection_status: "error",
  last_error_code: "identity_conflict",
};

// A response from before the 12B.2B deploy: the new fields are entirely
// absent, not merely null/empty.
const legacyOverview: AmazonConnectionOverview = {
  status: "NOT_CONNECTED",
  connection_status: "connected",
  persisted: true,
  provider: "SP_API",
  environment: "PRODUCTION",
  region: "na",
  marketplace: "amazon.com",
  application: "EWise",
  credentials_configured: true,
  selling_partner_id: "A1SELLERID",
  authorized_at: "2026-08-20T00:00:00.000Z",
  last_successful_validation_at: "2026-08-25T05:20:00.000Z",
  last_successful_sync_at: null,
  last_error_code: null,
  last_test_at: null,
  organization_id: fallbackOverview.organization_id,
  ads_api: { provider: "ADS_API", status: "NOT_CONNECTED" },
};

const consentUrl =
  "https://sellercentral.amazon.in/apps/authorize/consent?application_id=amzn1.sellerapps.app.test-app&state=csrf-state-token";

const authorizeStart: AmazonAuthorizationStart = {
  authorization_url: consentUrl,
  expires_at: "2026-08-23T04:00:00.000Z",
  connection_status: "pending_authorization",
  provider: "SP_API",
  environment: "SANDBOX",
  organization_id: fallbackOverview.organization_id,
};

const connected: AmazonConnectionTestResult = {
  status: "CONNECTED",
  provider: "SP_API",
  environment: "SANDBOX",
  marketplace: "amazon.in",
  operation: "getMarketplaceParticipations",
  tested_at: "2026-08-23T03:30:00.000Z",
  message: null,
};

const failed: AmazonConnectionTestResult = {
  status: "FAILED",
  provider: "SP_API",
  environment: "SANDBOX",
  marketplace: "amazon.in",
  operation: "getMarketplaceParticipations",
  tested_at: "2026-08-23T03:31:00.000Z",
  message: "Amazon SP-API sandbox authentication failed.",
};

const leakyOverview = {
  ...fallbackOverview,
  refresh_token: "Atzr|must-not-render",
  access_token: "Atza|must-not-render",
  token_reference: "asi:dev:must-not-render",
  client_secret: "must-not-render",
  client_id: "must-not-render",
} as AmazonConnectionOverview;

describe("Amazon Connection page", () => {
  let locationAssign: ReturnType<typeof vi.fn>;

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.history.replaceState({}, "", "/");
  });

  beforeEach(() => {
    locationAssign = vi.fn();
    vi.stubGlobal("location", {
      ...window.location,
      assign: locationAssign,
      search: "",
      href: "http://localhost:3000/connection",
      pathname: "/connection",
    });
    vi.mocked(fetchAmazonConnection).mockReset();
    vi.mocked(testAmazonConnection).mockReset();
    vi.mocked(authorizeAmazonConnection).mockReset();
    vi.mocked(fetchAmazonConnection).mockResolvedValue(fallbackOverview);
  });

  it("renders environment fallback view", async () => {
    render(<AmazonConnection />);
    expect(screen.getByRole("heading", { name: "Amazon Seller Connection (Beta)" })).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText("Environment fallback")).toBeInTheDocument();
    });
    expect(screen.getByText("NOT_CONNECTED")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Not connected", level: 2 })).toBeInTheDocument();
    expect(screen.getAllByText("Amazon SP-API").length).toBeGreaterThan(0);
    expect(screen.getByText("Production")).toBeInTheDocument();
    expect(screen.getByText("NA (US)")).toBeInTheDocument();
    expect(screen.getByText("Amazon.com")).toBeInTheDocument();
    expect(screen.getByText("EWise")).toBeInTheDocument();
    expect(screen.getByText("Not tested", { selector: "h3" })).toBeInTheDocument();
    expect(screen.getByText("Status: Not connected")).toBeInTheDocument();
    expect(screen.getByText("Status: Seller ingest not started")).toBeInTheDocument();
    expect(
      screen.getByText(/No saved Amazon connection. Showing environment defaults/),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Test Connection" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /dashboard/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Connect Amazon" })).toBeInTheDocument();
    expect(screen.getByText("No marketplaces synchronized yet")).toBeInTheDocument();
  });

  it("loads persisted connection status without treating it as sandbox success", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(persistedOverview);
    render(<AmazonConnection />);
    await waitFor(() => {
      expect(screen.getByText("Saved connection")).toBeInTheDocument();
    });
    expect(screen.getByText("NOT_CONNECTED")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Not connected", level: 2 })).toBeInTheDocument();
    // No display name on record: the same id appears for both "Seller account" and "Seller partner ID".
    expect(screen.getAllByText("A1SELLERID")).toHaveLength(2);
    expect(screen.getByText("Not tested", { selector: "h3" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "CONNECTED" })).not.toBeInTheDocument();
  });

  it("keeps persisted not-connected after a successful sandbox test", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(persistedOverview);
    vi.mocked(testAmazonConnection).mockResolvedValue(connected);
    render(<AmazonConnection />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Test Connection" })).toBeEnabled();
    });
    fireEvent.click(screen.getByRole("button", { name: "Test Connection" }));
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "CONNECTED" })).toBeInTheDocument();
    });
    expect(screen.getByText("NOT_CONNECTED")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Not connected", level: 2 })).toBeInTheDocument();
    expect(screen.getByText(/Checked/)).toBeInTheDocument();
    expect(screen.getByText("Grant validation succeeded.")).toBeInTheDocument();
    expect(screen.queryByText("Not tested")).not.toBeInTheDocument();
  });

  it("displays sandbox validation separately from persisted status", async () => {
    vi.mocked(testAmazonConnection).mockResolvedValue(connected);
    render(<AmazonConnection />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Test Connection" })).toBeEnabled();
    });
    expect(screen.getByText("Connection actions")).toBeInTheDocument();
    expect(screen.getByText("Connection status")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Test Connection" }));
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "CONNECTED" })).toBeInTheDocument();
    });
    expect(screen.getByText("NOT_CONNECTED")).toBeInTheDocument();
  });

  it("displays a failed sandbox test without exposing secrets", async () => {
    vi.mocked(testAmazonConnection).mockResolvedValue(failed);
    render(<AmazonConnection />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Test Connection" })).toBeEnabled();
    });
    fireEvent.click(screen.getByRole("button", { name: "Test Connection" }));
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "FAILED" })).toBeInTheDocument();
    });
    expect(screen.getByText("NOT_CONNECTED")).toBeInTheDocument();
    expect(screen.getByText("Amazon SP-API sandbox authentication failed.")).toBeInTheDocument();
    expect(screen.queryByText(/Atza\|/)).not.toBeInTheDocument();
    expect(screen.queryByText(/refresh_token/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/client_secret/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/token_reference/i)).not.toBeInTheDocument();
  });

  it("never renders secret fields from an unexpected payload", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(leakyOverview);
    render(<AmazonConnection />);
    await waitFor(() => {
      expect(screen.getByText("NOT_CONNECTED")).toBeInTheDocument();
    });
    expect(screen.queryByText(/Atzr\|/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Atza\|/)).not.toBeInTheDocument();
    expect(screen.queryByText(/asi:dev:must-not-render/)).not.toBeInTheDocument();
    expect(screen.queryByText(/refresh_token/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/access_token/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/token_reference/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/client_secret/i)).not.toBeInTheDocument();
  });

  it("handles load errors", async () => {
    vi.mocked(fetchAmazonConnection).mockReset();
    vi.mocked(fetchAmazonConnection).mockRejectedValue(
      new AmazonConnectionError(
        "Amazon Connection could not reach the server. Make sure the API is running.",
        "unavailable",
      ),
    );
    render(<AmazonConnection />);
    await waitFor(() => {
      expect(screen.getByText("Connection error")).toBeInTheDocument();
    });
    expect(
      screen.getByText("Amazon Connection could not reach the server. Make sure the API is running."),
    ).toBeInTheDocument();
  });

  it("calls authorize and redirects to the authorization URL", async () => {
    vi.mocked(authorizeAmazonConnection).mockResolvedValue(authorizeStart);
    render(<AmazonConnection />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Connect Amazon" })).toBeEnabled();
    });
    fireEvent.click(screen.getByRole("button", { name: "Connect Amazon" }));
    await waitFor(() => {
      expect(authorizeAmazonConnection).toHaveBeenCalledWith("PRODUCTION");
    });
    expect(locationAssign).toHaveBeenCalledWith(consentUrl);
    expect(screen.queryByText(consentUrl)).not.toBeInTheDocument();
    expect(screen.queryByText("csrf-state-token")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "CONNECTED" })).not.toBeInTheDocument();
  });

  it("shows a safe error when authorize fails", async () => {
    vi.mocked(authorizeAmazonConnection).mockRejectedValue(
      new AmazonConnectionError("Amazon SP-API request failed. client_secret=leak", "unknown"),
    );
    render(<AmazonConnection />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Connect Amazon" })).toBeEnabled();
    });
    fireEvent.click(screen.getByRole("button", { name: "Connect Amazon" }));
    await waitFor(() => {
      expect(screen.getByText("Unable to start Amazon connection. Please try again.")).toBeInTheDocument();
    });
    expect(locationAssign).not.toHaveBeenCalled();
    expect(screen.queryByText(/client_secret/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Amazon SP-API request failed/)).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "CONNECTED" })).not.toBeInTheDocument();
  });

  it("explains when the Amazon application id is not configured", async () => {
    vi.mocked(authorizeAmazonConnection).mockRejectedValue(
      new AmazonConnectionError("Amazon application is not configured.", "unavailable"),
    );
    render(<AmazonConnection />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Connect Amazon" })).toBeEnabled();
    });
    fireEvent.click(screen.getByRole("button", { name: "Connect Amazon" }));
    await waitFor(() => {
      expect(screen.getByText("Amazon application is not configured on the API.")).toBeInTheDocument();
    });
    expect(locationAssign).not.toHaveBeenCalled();
  });

  it("renders pending authorization without a Connect button or CONNECTED state", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(pendingOverview);
    render(<AmazonConnection />);
    await waitFor(() => {
      expect(screen.getByText("Waiting for Amazon authorization")).toBeInTheDocument();
    });
    expect(screen.getByText("PENDING_AUTHORIZATION")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Pending authorization", level: 2 })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Continue Amazon authorization" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "CONNECTED" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Test Connection" })).toBeInTheDocument();
  });

  it("asks to validate a pending seller grant and reloads overview after success", async () => {
    vi.mocked(fetchAmazonConnection)
      .mockResolvedValueOnce(pendingValidationOverview)
      .mockResolvedValueOnce(connectedOverview);
    vi.mocked(testAmazonConnection).mockResolvedValue({
      ...connected,
      environment: "PRODUCTION",
      marketplace: "amazon.com",
    });
    render(<AmazonConnection />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Validate connection" })).toBeEnabled();
    });
    expect(screen.getByText("Waiting for grant validation")).toBeInTheDocument();
    expect(
      screen.getByText(/Amazon authorization completed. Click Validate connection/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Test Connection" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "CONNECTED" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Validate connection" }));
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Connected", level: 2 })).toBeInTheDocument();
    });
    expect(testAmazonConnection).toHaveBeenCalledTimes(1);
    expect(fetchAmazonConnection).toHaveBeenCalledTimes(2);
    expect(screen.getAllByText("A1SELLERID").length).toBeGreaterThan(0);
    // Connected but never synchronized: the never-synchronized empty state renders.
    expect(screen.getByText("No marketplaces synchronized yet")).toBeInTheDocument();
  });

  it("asks to reconnect when the stored grant cannot be read", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(secretLostOverview);
    render(<AmazonConnection />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Connect Amazon again" })).toBeEnabled();
    });
    expect(screen.getByText("secret_access_failed")).toBeInTheDocument();
    expect(screen.getByText(/could not read the stored grant/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Validate connection" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "CONNECTED" })).not.toBeInTheDocument();
  });

  it("does not keep tokens or authorization URLs in the rendered tree", async () => {
    vi.mocked(authorizeAmazonConnection).mockResolvedValue({
      ...authorizeStart,
      authorization_url: `${consentUrl}&refresh_token=Atzr|must-not-render`,
    });
    render(<AmazonConnection />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Connect Amazon" })).toBeEnabled();
    });
    fireEvent.click(screen.getByRole("button", { name: "Connect Amazon" }));
    await waitFor(() => {
      expect(locationAssign).toHaveBeenCalled();
    });
    expect(screen.queryByText(/Atzr\|/)).not.toBeInTheDocument();
    expect(screen.queryByText(/refresh_token/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/access_token/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/token_reference/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/client_secret/i)).not.toBeInTheDocument();
  });

  it("prepares amazon return notices without treating them as connected", async () => {
    vi.stubGlobal("location", {
      ...window.location,
      assign: locationAssign,
      search: "?amazon=error&code=spapi_oauth_code_must_not_render",
      href: "http://localhost:3000/connection?amazon=error&code=spapi_oauth_code_must_not_render",
      pathname: "/connection",
    });
    render(<AmazonConnection />);
    await waitFor(() => {
      expect(screen.getByText("Amazon authorization could not be completed.")).toBeInTheDocument();
    });
    expect(screen.queryByText(/spapi_oauth_code/)).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "CONNECTED" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Connect Amazon" })).toBeInTheDocument();
  });

  describe("12B.2B — marketplace synchronization states", () => {
    it("latest successful run displays the success claim and its timestamp", async () => {
      vi.mocked(fetchAmazonConnection).mockResolvedValue(connectedSyncedOverview);
      render(<AmazonConnection />);
      await waitFor(() => {
        expect(screen.getByRole("heading", { name: "Connected", level: 2 })).toBeInTheDocument();
      });
      expect(
        screen.getByText(
          "Seller authorization is validated. Marketplace participation synchronized successfully at 25 Aug 2026, 11:30 am. Listings, orders, inventory, financials, and advertising data are not yet ingested.",
        ),
      ).toBeInTheDocument();
      expect(screen.getByText("BestSellerStore")).toBeInTheDocument();
      expect(screen.getAllByText("Amazon.com").length).toBeGreaterThan(0);
      expect(screen.getByText("US · www.amazon.com")).toBeInTheDocument();
      expect(screen.getAllByText("Last synchronized")[0]).toBeInTheDocument();
      expect(screen.getAllByText("25 Aug 2026, 11:30 am").length).toBeGreaterThan(0);
      expect(screen.getByRole("heading", { name: "Completed", level: 2 })).toBeInTheDocument();
      expect(screen.getByText("Marketplaces recorded")).toBeInTheDocument();
    });

    it("never claims synchronization happened when the connection has never synced", async () => {
      vi.mocked(fetchAmazonConnection).mockResolvedValue(connectedOverview);
      render(<AmazonConnection />);
      await waitFor(() => {
        expect(screen.getByRole("heading", { name: "Connected", level: 2 })).toBeInTheDocument();
      });
      expect(screen.queryByText(/participation synchronized successfully/)).not.toBeInTheDocument();
      expect(screen.getAllByText(/has never synchronized successfully/).length).toBeGreaterThan(0);
      expect(screen.getByText("No marketplaces synchronized yet")).toBeInTheDocument();
      expect(screen.getByText("No marketplace synchronization has run yet.")).toBeInTheDocument();
    });

    it("does not claim success when marketplace rows are present but no successful-sync timestamp exists", async () => {
      vi.mocked(fetchAmazonConnection).mockResolvedValue(marketplacesWithoutSuccessTimestampOverview);
      render(<AmazonConnection />);
      await waitFor(() => {
        expect(screen.getByRole("heading", { name: "Connected", level: 2 })).toBeInTheDocument();
      });
      // Marketplace presence alone must never establish synchronization success.
      expect(screen.queryByText(/participation synchronized successfully/)).not.toBeInTheDocument();
      expect(
        screen.getByText(
          "Seller authorization is validated. Marketplace participation has never synchronized successfully — click Test connection below to synchronize it.",
        ),
      ).toBeInTheDocument();
      // The (legacy/inconsistent) marketplace row still renders as data, just not as proof of success.
      expect(screen.getAllByText("Amazon.com").length).toBeGreaterThan(0);
      expect(screen.queryByText("Last synchronized")).not.toBeInTheDocument();
    });

    it("shows both facts when a prior success exists and the latest run failed", async () => {
      vi.mocked(fetchAmazonConnection).mockResolvedValue(failedSyncOverview);
      render(<AmazonConnection />);
      await waitFor(() => {
        expect(screen.getByRole("heading", { name: "Failed", level: 2 })).toBeInTheDocument();
      });
      expect(
        screen.getByText(
          "Seller authorization is validated. Marketplace data was last synchronized successfully at 25 Aug 2026, 11:30 am. The most recent synchronization attempt failed: A temporary internal error prevented saving marketplace data. This will retry on the next Test connection.",
        ),
      ).toBeInTheDocument();
      expect(
        screen.getByText(
          "The most recent synchronization attempt failed. Showing last-known marketplace data from 25 Aug 2026, 11:30 am.",
        ),
      ).toBeInTheDocument();
      // Previously synchronized marketplace data is still shown, not wiped out.
      expect(screen.getAllByText("Amazon.com").length).toBeGreaterThan(0);
      expect(screen.queryByText(/database_failure/)).not.toBeInTheDocument();
    });

    it("preserves last-known marketplace data while a new synchronization is running", async () => {
      vi.mocked(fetchAmazonConnection).mockResolvedValue(runningSyncOverview);
      render(<AmazonConnection />);
      await waitFor(() => {
        expect(screen.getByRole("heading", { name: "In progress", level: 2 })).toBeInTheDocument();
      });
      expect(
        screen.getByText(
          "Seller authorization is validated. A new marketplace synchronization is in progress. Showing marketplace data from the last successful synchronization at 25 Aug 2026, 11:30 am.",
        ),
      ).toBeInTheDocument();
      expect(
        screen.getByText(
          "Synchronization in progress. Showing last-known marketplace data from 25 Aug 2026, 11:30 am.",
        ),
      ).toBeInTheDocument();
      expect(screen.getAllByText("Amazon.com").length).toBeGreaterThan(0);
    });

    it("preserves a stale marketplace as inactive rather than hiding it", async () => {
      vi.mocked(fetchAmazonConnection).mockResolvedValue(staleMarketplaceOverview);
      render(<AmazonConnection />);
      await waitFor(() => {
        expect(screen.getByText("Amazon.ca")).toBeInTheDocument();
      });
      const staleCard = screen.getByText("Amazon.ca").closest("div")?.parentElement;
      expect(staleCard).not.toBeNull();
      expect(within(staleCard as HTMLElement).getByText("No longer reported")).toBeInTheDocument();
      // The still-current marketplace renders without that warning.
      expect(screen.getAllByText("Amazon.com").length).toBeGreaterThan(0);
    });

    it("shows a failed synchronization without erasing previously synced marketplaces", async () => {
      vi.mocked(fetchAmazonConnection).mockResolvedValue(failedSyncOverview);
      render(<AmazonConnection />);
      await waitFor(() => {
        expect(screen.getByRole("heading", { name: "Failed", level: 2 })).toBeInTheDocument();
      });
      expect(
        screen.getByText(
          "A temporary internal error prevented saving marketplace data. This will retry on the next Test connection.",
        ),
      ).toBeInTheDocument();
      // Previously synchronized marketplace data is still shown, not wiped out.
      expect(screen.getAllByText("Amazon.com").length).toBeGreaterThan(0);
      expect(screen.queryByText(/database_failure/)).not.toBeInTheDocument();
    });

    it("shows non-participating and suspended-listing warnings without total-failure language", async () => {
      vi.mocked(fetchAmazonConnection).mockResolvedValue(nonParticipatingAndSuspendedOverview);
      render(<AmazonConnection />);
      await waitFor(() => {
        expect(screen.getByText("Not participating")).toBeInTheDocument();
      });
      expect(screen.getByText("Suspended listings")).toBeInTheDocument();
      // The connection itself is still shown as Connected, not treated as failed.
      expect(screen.getByRole("heading", { name: "Connected", level: 2 })).toBeInTheDocument();
      expect(screen.queryByText("Needs attention")).not.toBeInTheDocument();
    });

    it("renders Needs attention only on an ownership conflict, without naming the other organization", async () => {
      vi.mocked(fetchAmazonConnection).mockResolvedValue(ownershipConflictOverview);
      render(<AmazonConnection />);
      await waitFor(() => {
        expect(screen.getByText("Needs attention")).toBeInTheDocument();
      });
      expect(
        screen.getByText(/already linked to a different ASI organization/),
      ).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "Error", level: 2 })).toBeInTheDocument();
      expect(screen.queryByText(/organization_id/i)).not.toBeInTheDocument();
    });

    it("renders Reauthorization required only when the backend reports requires_reauth", async () => {
      vi.mocked(fetchAmazonConnection).mockResolvedValue(reauthRequiredOverview);
      render(<AmazonConnection />);
      await waitFor(() => {
        expect(screen.getByText("Reauthorization required")).toBeInTheDocument();
      });
      expect(screen.getByRole("button", { name: "Connect Amazon" })).toBeInTheDocument();
    });

    it("renders a non-destructive Needs attention for a degraded connection", async () => {
      vi.mocked(fetchAmazonConnection).mockResolvedValue(degradedOverview);
      render(<AmazonConnection />);
      await waitFor(() => {
        expect(screen.getByText("Needs attention")).toBeInTheDocument();
      });
      expect(screen.getByText(/temporarily unavailable/)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Test Connection" })).toBeInTheDocument();
    });

    it("renders Reauthorization required when the stored seller identity is missing", async () => {
      vi.mocked(fetchAmazonConnection).mockResolvedValue(identityMissingOverview);
      render(<AmazonConnection />);
      await waitFor(() => {
        expect(screen.getByText("Reauthorization required")).toBeInTheDocument();
      });
      expect(screen.getByText(/identity is not available for this connection/)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Connect Amazon" })).toBeInTheDocument();
      // A missing identity must never read as a healthy, synchronized connection.
      expect(screen.queryByText(/participation synchronized successfully/)).not.toBeInTheDocument();
    });

    it("renders Needs attention for a stored-vs-result identity mismatch, without total-failure language", async () => {
      vi.mocked(fetchAmazonConnection).mockResolvedValue(identityConflictOverview);
      render(<AmazonConnection />);
      await waitFor(() => {
        expect(screen.getByText("Needs attention")).toBeInTheDocument();
      });
      expect(screen.getByText("Amazon seller identity could not be confirmed for this connection.")).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "Error", level: 2 })).toBeInTheDocument();
    });

    it("renders safely when the backend response predates 12B.2B and omits the new fields", async () => {
      vi.mocked(fetchAmazonConnection).mockResolvedValue(legacyOverview);
      render(<AmazonConnection />);
      await waitFor(() => {
        expect(screen.getByRole("heading", { name: "Connected", level: 2 })).toBeInTheDocument();
      });
      expect(screen.getByText("No marketplaces synchronized yet")).toBeInTheDocument();
      expect(screen.getByText("No marketplace synchronization has run yet.")).toBeInTheDocument();
      expect(screen.queryByText("undefined")).not.toBeInTheDocument();
      expect(screen.queryByText("null")).not.toBeInTheDocument();
    });
  });
});
