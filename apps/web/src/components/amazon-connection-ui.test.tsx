import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
import type { AmazonAuthorizationStart, AmazonConnectionOverview, AmazonConnectionTestResult } from "@/lib/types";

const fallbackOverview: AmazonConnectionOverview = {
  status: "NOT_CONNECTED",
  connection_status: "not_connected",
  persisted: false,
  provider: "SP_API",
  environment: "SANDBOX",
  region: "eu",
  marketplace: "amazon.in",
  application: "EWise",
  credentials_configured: true,
  selling_partner_id: null,
  authorized_at: null,
  last_successful_validation_at: null,
  last_successful_sync_at: null,
  last_error_code: null,
  last_test_at: null,
  organization_id: "11111111-1111-4111-8111-111111111111",
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
    expect(screen.getByText("Not connected")).toBeInTheDocument();
    expect(screen.getAllByText("Amazon SP-API").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Sandbox").length).toBeGreaterThan(0);
    expect(screen.getByText("EU")).toBeInTheDocument();
    expect(screen.getByText("Amazon.in")).toBeInTheDocument();
    expect(screen.getByText("EWise")).toBeInTheDocument();
    expect(screen.getByText("Not tested", { selector: "h2" })).toBeInTheDocument();
    expect(screen.getByText("Status: Not connected")).toBeInTheDocument();
    expect(
      screen.getByText(/No saved Amazon connection. Showing environment defaults/),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Test Connection" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /dashboard/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Connect Amazon" })).toBeInTheDocument();
  });

  it("loads persisted connection status without treating it as sandbox success", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(persistedOverview);
    render(<AmazonConnection />);
    await waitFor(() => {
      expect(screen.getByText("Saved connection")).toBeInTheDocument();
    });
    expect(screen.getByText("NOT_CONNECTED")).toBeInTheDocument();
    expect(screen.getByText("Not connected")).toBeInTheDocument();
    expect(screen.getByText("A1SELLERID")).toBeInTheDocument();
    expect(screen.getByText("Not tested", { selector: "h2" })).toBeInTheDocument();
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
    expect(screen.getByText("Not connected")).toBeInTheDocument();
    expect(screen.getByText("Validated successfully at")).toBeInTheDocument();
    expect(screen.getByText("Sandbox validation succeeded. This is not seller authorization.")).toBeInTheDocument();
    expect(screen.queryByText("Not tested")).not.toBeInTheDocument();
  });

  it("displays sandbox validation separately from persisted status", async () => {
    vi.mocked(testAmazonConnection).mockResolvedValue(connected);
    render(<AmazonConnection />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Test Connection" })).toBeEnabled();
    });
    expect(screen.getByText("Latest sandbox validation")).toBeInTheDocument();
    expect(screen.getByText("Amazon connection")).toBeInTheDocument();
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
      expect(authorizeAmazonConnection).toHaveBeenCalledWith("SANDBOX");
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
    expect(screen.getByText("Pending authorization")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Continue Amazon authorization" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "CONNECTED" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Test Connection" })).toBeInTheDocument();
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
});
