import * as React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/link", () => ({
  default: React.forwardRef<HTMLAnchorElement, { href: string; children: React.ReactNode }>(
    ({ href, children, ...rest }, ref) => (
      <a href={href} ref={ref} {...rest}>
        {children}
      </a>
    ),
  ),
}));

vi.mock("@/lib/api", () => ({
  fetchUsageDashboard: vi.fn().mockResolvedValue({
    provider: "mock",
    budget_usd: 0,
    spent_usd: 0,
    remaining_usd: 0,
    events: [],
  }),
  fetchAmazonConnection: vi.fn().mockResolvedValue({
    connection_status: "not_connected",
  }),
}));

import { AppShell } from "@/components/app-shell";

beforeEach(() => {
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

describe("AppShell — 12B.4D five-destination navigation", () => {
  it("renders exactly the five primary destinations", () => {
    render(
      <AppShell current="analyze">
        <div>content</div>
      </AppShell>,
    );
    for (const label of [/^analyze$/i, /^copilot$/i, /^seller$/i, /^analytics$/i, /^activity$/i]) {
      expect(screen.getAllByRole("link", { name: label }).length).toBeGreaterThan(0);
    }
  });

  it("marks the active destination with aria-current, desktop nav only", () => {
    render(
      <AppShell current="seller">
        <div>content</div>
      </AppShell>,
    );
    const sellerLinks = screen.getAllByRole("link", { name: /^seller$/i });
    expect(sellerLinks.some((el) => el.getAttribute("aria-current") === "page")).toBe(true);
    const analyzeLinks = screen.getAllByRole("link", { name: /^analyze$/i });
    expect(analyzeLinks.every((el) => el.getAttribute("aria-current") !== "page")).toBe(true);
  });

  it("Seller links to /seller, not the old /seller-listings route", () => {
    render(
      <AppShell current="seller">
        <div>content</div>
      </AppShell>,
    );
    const sellerLinks = screen.getAllByRole("link", { name: /^seller$/i });
    expect(sellerLinks.some((el) => el.getAttribute("href") === "/seller")).toBe(true);
  });

  it("preserves active-link styling", () => {
    render(
      <AppShell current="analytics">
        <div>content</div>
      </AppShell>,
    );
    const activeLink = screen.getAllByRole("link", { name: /^analytics$/i })[0];
    expect(activeLink.className).toContain("border-primary");
    const inactiveLink = screen.getAllByRole("link", { name: /^analyze$/i })[0];
    expect(inactiveLink.className).toContain("border-transparent");
  });

  it("keeps every nav link keyboard-focusable", () => {
    render(
      <AppShell current="activity">
        <div>content</div>
      </AppShell>,
    );
    const links = screen.getAllByRole("link").filter((el) => el.getAttribute("href") !== "/");
    for (const link of links) {
      expect(link.tagName).toBe("A");
      expect(link).not.toHaveAttribute("tabindex", "-1");
    }
  });

  it("does not require horizontal scrolling for the desktop primary nav", () => {
    render(
      <AppShell current="copilot">
        <div>content</div>
      </AppShell>,
    );
    const nav = screen.getByLabelText("Primary");
    // The old design used overflow-x-auto to scroll among eight items;
    // five items fit without it.
    expect(nav.className).not.toContain("overflow-x-auto");
  });

  it("renders a mobile More trigger distinct from the five primary destinations", () => {
    render(
      <AppShell current="analyze">
        <div>content</div>
      </AppShell>,
    );
    expect(screen.getByRole("button", { name: /more/i })).toBeInTheDocument();
  });

  it("moves Connection out of primary navigation into the account menu", () => {
    render(
      <AppShell current="analyze">
        <div>content</div>
      </AppShell>,
    );
    expect(screen.queryByRole("link", { name: /^connection$/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /account and settings/i })).toBeInTheDocument();
  });

  it("uses the compact (icon-only) navigation mode below the xl breakpoint, expanding to full labels at xl+ — the 1024px overflow fix", () => {
    render(
      <AppShell current="activity">
        <div>content</div>
      </AppShell>,
    );
    const nav = screen.getByLabelText("Primary");
    const labelSpans = Array.from(nav.querySelectorAll("a > span"));
    // Five primary destinations, each with a dedicated label span.
    expect(labelSpans.length).toBe(5);
    for (const span of labelSpans) {
      // Compact-header treatment: the label is hidden (not shrunk to an
      // unreadable size) below `xl` and reinstated at `xl:` — this is
      // the intended navigation mode at 1024px (below `xl`, 1280px),
      // and what previously overflowed by rendering full labels there.
      expect(span.className).toContain("hidden");
      expect(span.className).toContain("xl:inline");
    }
    // Every primary link keeps a visible icon and a real accessible
    // name regardless of whether its text label is currently hidden.
    for (const label of [/^analyze$/i, /^copilot$/i, /^seller$/i, /^analytics$/i, /^activity$/i]) {
      const link = screen.getAllByRole("link", { name: label })[0];
      expect(link.querySelector("svg")).not.toBeNull();
    }
  });

  it("hides Analyze/Analytics secondary links below xl too, so they cannot reintroduce the same compact-width overflow", () => {
    render(
      <AppShell current="analytics">
        <div>content</div>
      </AppShell>,
    );
    const secondaryLink = screen.getByRole("link", { name: /seller reports/i });
    const secondaryContainer = secondaryLink.parentElement;
    expect(secondaryContainer?.className).toContain("hidden");
    expect(secondaryContainer?.className).toContain("xl:flex");
    expect(secondaryContainer?.className).not.toContain("md:flex");
  });
});
