import * as React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/link", () => ({
  default: React.forwardRef<HTMLAnchorElement, { href: string; children: React.ReactNode }>(
    ({ href, children, ...rest }, ref) => (
      <a href={href} ref={ref} {...rest}>
        {children}
      </a>
    ),
  ),
}));

const { useSearchParamsMock } = vi.hoisted(() => ({ useSearchParamsMock: vi.fn() }));
vi.mock("next/navigation", () => ({
  useSearchParams: useSearchParamsMock,
}));

import { SellerLocalNav } from "@/components/seller-local-nav";

afterEach(() => {
  cleanup();
  useSearchParamsMock.mockReset();
});

describe("SellerLocalNav", () => {
  it("renders Overview, Listings, Orders, and Sales & Traffic tabs", () => {
    useSearchParamsMock.mockReturnValue(new URLSearchParams());
    render(<SellerLocalNav active="overview" />);
    expect(screen.getByRole("link", { name: "Overview" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Listings" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Orders" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sales & Traffic" })).toBeInTheDocument();
  });

  it("marks the Sales & Traffic tab active with aria-current", () => {
    useSearchParamsMock.mockReturnValue(new URLSearchParams());
    render(<SellerLocalNav active="sales-traffic" />);
    expect(screen.getByRole("link", { name: "Sales & Traffic" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Orders" })).not.toHaveAttribute("aria-current");
  });

  it("marks the active tab with aria-current", () => {
    useSearchParamsMock.mockReturnValue(new URLSearchParams());
    render(<SellerLocalNav active="orders" />);
    expect(screen.getByRole("link", { name: "Orders" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Listings" })).not.toHaveAttribute("aria-current");
  });

  it("preserves the selected participation query param across tabs", () => {
    useSearchParamsMock.mockReturnValue(new URLSearchParams("participation=abc-123"));
    render(<SellerLocalNav active="listings" />);
    expect(screen.getByRole("link", { name: "Orders" })).toHaveAttribute(
      "href",
      "/seller/orders?participation=abc-123",
    );
    expect(screen.getByRole("link", { name: "Overview" })).toHaveAttribute(
      "href",
      "/seller?participation=abc-123",
    );
  });

  it("omits the query string entirely when no participation is selected", () => {
    useSearchParamsMock.mockReturnValue(new URLSearchParams());
    render(<SellerLocalNav active="overview" />);
    expect(screen.getByRole("link", { name: "Listings" })).toHaveAttribute("href", "/seller/listings");
  });
});
