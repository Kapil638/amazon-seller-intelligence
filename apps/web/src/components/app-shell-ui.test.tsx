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

describe("AppShell active-link visibility", () => {
  it("scrolls the active link into view within the nav container on initial render", () => {
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;

    render(
      <AppShell current="seller-listings">
        <div>content</div>
      </AppShell>,
    );

    const activeLink = screen.getByRole("link", { name: /seller data/i });
    expect(scrollIntoView).toHaveBeenCalledWith(
      expect.objectContaining({ block: "nearest", inline: "nearest" }),
    );
    // Called on the active link's own element, not some other node.
    expect(scrollIntoView.mock.instances[0]).toBe(activeLink);
  });

  it("re-scrolls when the active destination changes", () => {
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;

    const { rerender } = render(
      <AppShell current="asin">
        <div>content</div>
      </AppShell>,
    );
    const callsAfterFirstRender = scrollIntoView.mock.calls.length;
    expect(callsAfterFirstRender).toBeGreaterThan(0);

    rerender(
      <AppShell current="bulk">
        <div>content</div>
      </AppShell>,
    );

    expect(scrollIntoView.mock.calls.length).toBeGreaterThan(callsAfterFirstRender);
    const bulkLink = screen.getByRole("link", { name: /bulk due diligence/i });
    expect(scrollIntoView.mock.instances.at(-1)).toBe(bulkLink);
  });

  it("marks the active link with aria-current for assistive technology", () => {
    Element.prototype.scrollIntoView = vi.fn();
    render(
      <AppShell current="connection">
        <div>content</div>
      </AppShell>,
    );
    expect(screen.getByRole("link", { name: /^connection$/i })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: /^analyze$/i })).not.toHaveAttribute("aria-current");
  });

  it("preserves active-link styling", () => {
    Element.prototype.scrollIntoView = vi.fn();
    render(
      <AppShell current="profit">
        <div>content</div>
      </AppShell>,
    );
    const activeLink = screen.getByRole("link", { name: /^profit$/i });
    expect(activeLink.className).toContain("border-primary");
    const inactiveLink = screen.getByRole("link", { name: /^analyze$/i });
    expect(inactiveLink.className).toContain("border-transparent");
  });

  it("keeps every nav link keyboard-focusable", () => {
    Element.prototype.scrollIntoView = vi.fn();
    render(
      <AppShell current="history">
        <div>content</div>
      </AppShell>,
    );
    const links = screen.getAllByRole("link").filter((el) => el.getAttribute("href") !== "/");
    // Every destination is a real, focusable <a> (no tabIndex=-1, no button-as-div substitution).
    for (const link of links) {
      expect(link.tagName).toBe("A");
      expect(link).not.toHaveAttribute("tabindex", "-1");
    }
  });

  it("does not change desktop layout structure", () => {
    Element.prototype.scrollIntoView = vi.fn();
    render(
      <AppShell current="reports">
        <div>content</div>
      </AppShell>,
    );
    const nav = screen.getByRole("navigation");
    expect(nav.className).toContain("overflow-x-auto");
    expect(nav.className).toContain("flex");
  });
});
