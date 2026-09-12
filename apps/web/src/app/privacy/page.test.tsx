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

import PrivacyPage from "@/app/privacy/page";

afterEach(cleanup);

describe("PrivacyPage", () => {
  it("renders the Privacy Notice heading and last-updated date", () => {
    render(<PrivacyPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Privacy Notice" })).toBeInTheDocument();
    expect(screen.getByText("Last updated: September 12, 2026")).toBeInTheDocument();
  });

  it("states the correct operator legal entity and contact details", () => {
    render(<PrivacyPage />);
    expect(screen.getAllByText(/Ewisepartners LLC/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/EWISE Partners/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Bonney Lake, WA 98391/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/info@ewisepartners\.com/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/\(630\) 261-5987/).length).toBeGreaterThan(0);
  });

  it("does not contain a [PENDING] placeholder", () => {
    const { container } = render(<PrivacyPage />);
    expect(container.textContent).not.toContain("[PENDING");
  });

  it("does not gate content behind an authentication/draft notice", () => {
    render(<PrivacyPage />);
    expect(screen.queryByText(/Private pilot\./)).not.toBeInTheDocument();
  });

  it("exposes visible Privacy and Terms links via the shared public footer", () => {
    render(<PrivacyPage />);
    const footer = screen.getByRole("contentinfo");
    expect(footer.querySelector('a[href="/privacy"]')).not.toBeNull();
    expect(footer.querySelector('a[href="/terms"]')).not.toBeNull();
  });
});
