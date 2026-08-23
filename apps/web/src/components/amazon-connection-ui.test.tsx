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
}));

import { AmazonConnection } from "@/components/amazon-connection";
import { AmazonConnectionError, fetchAmazonConnection, testAmazonConnection } from "@/lib/api";
import type { AmazonConnectionOverview, AmazonConnectionTestResult } from "@/lib/types";

const overview: AmazonConnectionOverview = {
  status: "NOT_CONNECTED",
  provider: "SP_API",
  environment: "SANDBOX",
  marketplace: "amazon.in",
  application: "EWise",
  credentials_configured: true,
  last_test_at: null,
  organization_id: "11111111-1111-4111-8111-111111111111",
  ads_api: { provider: "ADS_API", status: "NOT_CONNECTED" },
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

describe("Amazon Connection page", () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    vi.mocked(fetchAmazonConnection).mockReset();
    vi.mocked(testAmazonConnection).mockReset();
    vi.mocked(fetchAmazonConnection).mockResolvedValue(overview);
  });

  it("renders connection metadata and not-connected status", async () => {
    render(<AmazonConnection />);
    expect(screen.getByRole("heading", { name: "Amazon Seller Connection (Beta)" })).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText("Not connected")).toBeInTheDocument();
    });
    expect(screen.getAllByText("Amazon SP-API").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Sandbox").length).toBeGreaterThan(0);
    expect(screen.getByText("Amazon.in")).toBeInTheDocument();
    expect(screen.getByText("EWise")).toBeInTheDocument();
    expect(screen.getByText("Not tested")).toBeInTheDocument();
    expect(screen.getByText("Status: Not connected")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Test Connection" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /dashboard/i })).not.toBeInTheDocument();
  });

  it("displays connected status after a successful test", async () => {
    vi.mocked(testAmazonConnection).mockResolvedValue(connected);
    render(<AmazonConnection />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Test Connection" })).toBeEnabled();
    });
    fireEvent.click(screen.getByRole("button", { name: "Test Connection" }));
    await waitFor(() => {
      expect(screen.getByText("Connected")).toBeInTheDocument();
    });
    expect(screen.queryByText("Not tested")).not.toBeInTheDocument();
  });

  it("displays a failed connection test without exposing secrets", async () => {
    vi.mocked(testAmazonConnection).mockResolvedValue(failed);
    render(<AmazonConnection />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Test Connection" })).toBeEnabled();
    });
    fireEvent.click(screen.getByRole("button", { name: "Test Connection" }));
    await waitFor(() => {
      expect(screen.getByText("Connection failed")).toBeInTheDocument();
    });
    expect(screen.getByText("Amazon SP-API sandbox authentication failed.")).toBeInTheDocument();
    expect(screen.queryByText(/Atza\|/)).not.toBeInTheDocument();
    expect(screen.queryByText(/refresh_token/i)).not.toBeInTheDocument();
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
});
