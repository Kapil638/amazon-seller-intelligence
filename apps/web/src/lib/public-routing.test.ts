import { describe, expect, it } from "vitest";

import { marketingHosts, resolvePublicRouting } from "@/lib/public-routing";

describe("resolvePublicRouting", () => {
  it("passes through unchanged for the app subdomain", () => {
    expect(resolvePublicRouting("app.ewiseintelligence.com", "/seller/inventory")).toEqual({ action: "next" });
  });

  it("passes through unchanged for localhost (local dev is never a marketing host)", () => {
    expect(resolvePublicRouting("localhost:3000", "/")).toEqual({ action: "next" });
  });

  it("rewrites the bare marketing domain's root to the internal marketing route", () => {
    expect(resolvePublicRouting("ewiseintelligence.com", "/")).toEqual({
      action: "rewrite",
      pathname: "/marketing",
    });
  });

  it("rewrites the www marketing host's root the same way", () => {
    expect(resolvePublicRouting("www.ewiseintelligence.com", "/")).toEqual({
      action: "rewrite",
      pathname: "/marketing",
    });
  });

  it("serves /privacy identically on the marketing host — never rewritten or redirected", () => {
    expect(resolvePublicRouting("ewiseintelligence.com", "/privacy")).toEqual({ action: "next" });
  });

  it("serves /terms identically on the marketing host", () => {
    expect(resolvePublicRouting("ewiseintelligence.com", "/terms")).toEqual({ action: "next" });
  });

  it("redirects any other path on the marketing host to the app subdomain — never exposes app routes publicly", () => {
    expect(resolvePublicRouting("ewiseintelligence.com", "/seller/inventory")).toEqual({
      action: "redirect",
      host: "app.ewiseintelligence.com",
    });
  });

  it("redirects an API-shaped path on the marketing host too — the public hostname must never serve internal routes", () => {
    expect(resolvePublicRouting("ewiseintelligence.com", "/connection")).toEqual({
      action: "redirect",
      host: "app.ewiseintelligence.com",
    });
  });

  it("strips a leading www. when building the redirect target host", () => {
    expect(resolvePublicRouting("www.ewiseintelligence.com", "/seller")).toEqual({
      action: "redirect",
      host: "app.ewiseintelligence.com",
    });
  });

  it("strips a port from the Host header before matching", () => {
    expect(resolvePublicRouting("ewiseintelligence.com:443", "/")).toEqual({
      action: "rewrite",
      pathname: "/marketing",
    });
  });

  it("respects a NEXT_PUBLIC_MARKETING_HOSTS override, replacing the default list", () => {
    expect(resolvePublicRouting("staging-marketing.example.com", "/", "staging-marketing.example.com")).toEqual({
      action: "rewrite",
      pathname: "/marketing",
    });
    // The real production host is no longer recognized once an override
    // is set — an override replaces, never merely adds to, the default.
    expect(resolvePublicRouting("ewiseintelligence.com", "/", "staging-marketing.example.com")).toEqual({
      action: "next",
    });
  });

  it("falls back to the default list when the override is empty/whitespace-only", () => {
    expect(resolvePublicRouting("ewiseintelligence.com", "/", "   ")).toEqual({
      action: "rewrite",
      pathname: "/marketing",
    });
  });
});

describe("marketingHosts", () => {
  it("returns the built-in default when no override is given", () => {
    expect(marketingHosts(undefined)).toEqual(["ewiseintelligence.com", "www.ewiseintelligence.com"]);
  });

  it("parses a comma-separated override, trimming whitespace and lowercasing", () => {
    expect(marketingHosts(" Example.com , Other.example.com ")).toEqual(["example.com", "other.example.com"]);
  });
});
