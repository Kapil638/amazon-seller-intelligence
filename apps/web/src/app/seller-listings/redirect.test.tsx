import { describe, expect, it, vi } from "vitest";

const { redirectMock } = vi.hoisted(() => ({ redirectMock: vi.fn() }));
vi.mock("next/navigation", () => ({
  redirect: redirectMock,
}));

import SellerListingsRedirectPage from "./page";

describe("12B.4D — /seller-listings backward-compatibility redirect", () => {
  it("redirects to /seller/listings with no query params", async () => {
    redirectMock.mockClear();
    await SellerListingsRedirectPage({ searchParams: Promise.resolve({}) });
    expect(redirectMock).toHaveBeenCalledWith("/seller/listings");
  });

  it("preserves a selected participation and other query parameters", async () => {
    redirectMock.mockClear();
    await SellerListingsRedirectPage({
      searchParams: Promise.resolve({ participation: "abc-123", listing: "def-456" }),
    });
    const target = redirectMock.mock.calls[0][0] as string;
    expect(target.startsWith("/seller/listings?")).toBe(true);
    const query = new URLSearchParams(target.split("?")[1]);
    expect(query.get("participation")).toBe("abc-123");
    expect(query.get("listing")).toBe("def-456");
  });

  it("preserves repeated query parameter values", async () => {
    redirectMock.mockClear();
    await SellerListingsRedirectPage({
      searchParams: Promise.resolve({ tag: ["a", "b"] }),
    });
    const target = redirectMock.mock.calls[0][0] as string;
    const query = new URLSearchParams(target.split("?")[1]);
    expect(query.getAll("tag")).toEqual(["a", "b"]);
  });
});
