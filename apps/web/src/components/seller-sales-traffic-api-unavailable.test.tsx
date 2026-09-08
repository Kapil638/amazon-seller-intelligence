import * as React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// fix/supervise-ingestion-runtime — the live defect this test locks in:
// navigating to /seller/sales-traffic while the API is unreachable (the
// exact symptom reported live: "http://localhost:8000/health ... failed
// to connect") must render a clear connection-error message, never a
// blank page. `SellerSalesTraffic` already had a `connectionError` state
// for this before this fix (see seller-sales-traffic.tsx's first
// `useEffect`) — this test exists because nothing previously asserted it,
// so a future regression here would have gone unnoticed exactly like the
// live incident did. Defense-in-depth for a render-time throw instead of
// a rejected fetch is covered separately by `error.tsx`/`global-error.tsx`,
// which cannot be exercised through RTL `render()` the same way Next.js's
// own router does.

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
  usePathname: () => "/seller/sales-traffic",
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
  SalesTrafficApiError: class SalesTrafficApiError extends Error {
    kind: string;
    constructor(message: string, kind = "unknown") {
      super(message);
      this.kind = kind;
    }
  },
  fetchAmazonConnection: vi.fn(),
  fetchSalesTrafficDailyTrend: vi.fn(),
  fetchSalesTrafficFreshness: vi.fn(),
  fetchSalesTrafficProducts: vi.fn(),
  fetchSalesTrafficSummary: vi.fn(),
  triggerSalesTrafficSync: vi.fn(),
}));

import { SellerSalesTraffic } from "@/components/seller-sales-traffic";
import { AmazonConnectionError, fetchAmazonConnection } from "@/lib/api";

beforeEach(() => {
  vi.clearAllMocks();
  setMockSearch("");
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

describe("Sales & Traffic — API unavailable", () => {
  it("shows a connection-error message instead of a blank page when the API cannot be reached", async () => {
    // A real "API is down" fetch failure surfaces to callers as a network
    // TypeError, not any typed API error class — vanilla fetch behavior
    // this component's .catch() must still handle.
    vi.mocked(fetchAmazonConnection).mockRejectedValue(new TypeError("Failed to fetch"));

    render(<SellerSalesTraffic />);

    await waitFor(() => expect(screen.getByText("Amazon Connection could not be reached.")).toBeInTheDocument());
    // The page must render *something* meaningful, not nothing.
    expect(document.body.textContent).not.toBe("");
  });

  it("shows the connection's own sanitized error message when it is a typed AmazonConnectionError", async () => {
    vi.mocked(fetchAmazonConnection).mockRejectedValue(new AmazonConnectionError("Connection not found.", "not_found"));

    render(<SellerSalesTraffic />);

    await waitFor(() => expect(screen.getByText("Connection not found.")).toBeInTheDocument());
  });
});
