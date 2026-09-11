/**
 * Final review gate, PR #28 — every request api.ts makes targets a
 * different origin from the frontend itself in any deployed environment
 * (and in local dev — see apiFetch's own comment in api.ts). Cloudflare
 * Access (pilot-deployment-ewise, correction 2) authenticates a browser
 * via a session cookie; a cross-origin fetch() sends no cookies at all
 * unless `credentials: "include"` is set explicitly. This file proves
 * every one of api.ts's exported request functions actually goes through
 * apiFetch (and therefore always sets credentials: "include"), rather
 * than trusting that by inspection alone — a future call site added with
 * a bare `fetch(...)` instead of `apiFetch(...)` would silently reopen
 * exactly this gap, and would not be caught by TypeScript or any other
 * existing test.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const jsonResponse = (body: unknown, init?: ResponseInit) =>
  new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" }, ...init });

describe("api.ts requests always send credentials: include", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    process.env.NEXT_PUBLIC_API_BASE_URL = "https://api.ewiseintelligence.com";
    fetchMock = vi.fn().mockResolvedValue(jsonResponse({}));
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  function assertEveryCallIncludedCredentials() {
    expect(fetchMock.mock.calls.length).toBeGreaterThan(0);
    for (const call of fetchMock.mock.calls) {
      const init = call[1] as RequestInit | undefined;
      expect(init).toBeDefined();
      expect(init?.credentials).toBe("include");
    }
  }

  it("fetchWorkerHealth sends credentials: include", async () => {
    const { fetchWorkerHealth } = await import("@/lib/api");
    await fetchWorkerHealth();
    assertEveryCallIncludedCredentials();
  });

  it("fetchAmazonConnection sends credentials: include", async () => {
    const { fetchAmazonConnection } = await import("@/lib/api");
    await fetchAmazonConnection();
    assertEveryCallIncludedCredentials();
  });

  it("testAmazonConnection sends credentials: include", async () => {
    const { testAmazonConnection } = await import("@/lib/api");
    await testAmazonConnection();
    assertEveryCallIncludedCredentials();
  });

  it("authorizeAmazonConnection sends credentials: include", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        authorization_url: "https://sellercentral.amazon.com/apps/authorize/consent?application_id=x&state=y",
        expires_at: new Date().toISOString(),
        connection_status: "pending_authorization",
        provider: "SP_API",
        environment: "PRODUCTION",
        organization_id: "11111111-1111-4111-8111-111111111111",
      }),
    );
    const { authorizeAmazonConnection } = await import("@/lib/api");
    await authorizeAmazonConnection("PRODUCTION");
    assertEveryCallIncludedCredentials();
  });

  it("fetchProduct sends credentials: include", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ asin: "B000000000", marketplace: "amazon.in" }));
    const { fetchProduct } = await import("@/lib/api");
    await fetchProduct("B000000000").catch(() => undefined);
    assertEveryCallIncludedCredentials();
  });

  it("fetchUsageDashboard sends credentials: include", async () => {
    const { fetchUsageDashboard } = await import("@/lib/api");
    await fetchUsageDashboard().catch(() => undefined);
    assertEveryCallIncludedCredentials();
  });

  it("no exported request function bypasses apiFetch with a bare global fetch call missing credentials", async () => {
    // Cross-check against the module source itself: every fetch( call
    // site must be the apiFetch( wrapper, never the bare global — the
    // definitive, low-level guard against a future regression, since the
    // functional tests above only sample a handful of the ~35 call
    // sites in this file.
    const fs = await import("node:fs");
    const path = await import("node:path");
    const source = fs.readFileSync(path.join(process.cwd(), "src/lib/api.ts"), "utf-8");
    const bareFetchCalls = source
      .split("\n")
      .filter(
        (line) =>
          /(?<![A-Za-z0-9_])fetch\(/.test(line) &&
          !/apiFetch\(/.test(line) &&
          !line.trim().startsWith("//") &&
          // The apiFetch wrapper's own single legitimate internal call to
          // the real global fetch — the one place a bare call belongs.
          !line.includes("return fetch(input"),
      );
    expect(bareFetchCalls).toEqual([]);
  });
});
