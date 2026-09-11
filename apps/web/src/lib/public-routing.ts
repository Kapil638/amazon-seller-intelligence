/**
 * pilot-deployment-ewise — pure routing-decision logic for the public
 * marketing/legal domain (`ewiseintelligence.com`) vs. the private app
 * domain (`app.ewiseintelligence.com`), both served by this one Next.js
 * deployment to keep pilot infrastructure costs down (one Railway
 * frontend service, two custom domains attached to it, Cloudflare
 * Access scoped to the app.* hostname only).
 *
 * Kept as a pure function of (host, pathname) — no `next/server` import
 * here — specifically so it is directly unit-testable without needing
 * to construct a real `NextRequest`/Edge runtime. `middleware.ts` is a
 * thin wrapper around this.
 */

export type PublicRoutingDecision =
  | { action: "next" }
  | { action: "rewrite"; pathname: string }
  | { action: "redirect"; host: string };

// A comma-separated NEXT_PUBLIC_MARKETING_HOSTS env override widens or
// replaces this default — see next.config / deployment docs. Empty on
// local dev by construction (no request ever arrives with this Host),
// so this never changes local/dev/test behavior.
const DEFAULT_MARKETING_HOSTS = ["ewiseintelligence.com", "www.ewiseintelligence.com"];

// Paths that are genuinely public — served identically regardless of
// which of the two hostnames the request arrived on. Never includes
// anything under the private app's own routes.
const PUBLIC_PATHS = new Set(["/", "/privacy", "/terms"]);

const MARKETING_LANDING_REWRITE_PATH = "/marketing";

function normalizeHost(host: string): string {
  return host.split(":")[0]?.toLowerCase() ?? "";
}

export function marketingHosts(envOverride: string | undefined): string[] {
  if (!envOverride) return DEFAULT_MARKETING_HOSTS;
  const parsed = envOverride
    .split(",")
    .map((h) => h.trim().toLowerCase())
    .filter(Boolean);
  return parsed.length > 0 ? parsed : DEFAULT_MARKETING_HOSTS;
}

/**
 * Decides what a request should do based purely on its Host header and
 * pathname:
 * - The bare marketing domain, requesting `/`: rewritten internally to
 *   the marketing landing page content (never redirected — the
 *   published URL a visitor sees must stay `ewiseintelligence.com`).
 * - The bare marketing domain, requesting `/privacy` or `/terms`:
 *   passed through unchanged — these routes serve identically on both
 *   hostnames, so there is exactly one copy of this content, never two.
 * - The bare marketing domain, requesting anything else (an app route):
 *   redirected to the same path on the app subdomain — the public
 *   hostname must never expose the private app's own surface, even by
 *   accident (matches the "do not accidentally expose internal APIs"
 *   requirement for the Cloudflare Access boundary this mirrors).
 * - Any other Host (app.ewiseintelligence.com, localhost, a preview
 *   deploy, anything unrecognized): passed through unchanged — the
 *   full private app, exactly as it already behaves today.
 */
export function resolvePublicRouting(
  host: string,
  pathname: string,
  envOverride?: string,
): PublicRoutingDecision {
  const normalizedHost = normalizeHost(host);
  const hosts = marketingHosts(envOverride);
  if (!hosts.includes(normalizedHost)) {
    return { action: "next" };
  }
  if (pathname === "/") {
    return { action: "rewrite", pathname: MARKETING_LANDING_REWRITE_PATH };
  }
  if (PUBLIC_PATHS.has(pathname)) {
    return { action: "next" };
  }
  // Any app-hostname override should not itself be marketing-hosted —
  // callers pass the *app* host explicitly rather than this module
  // guessing one from the marketing host, since "ewiseintelligence.com"
  // -> "app.ewiseintelligence.com" is a naming convention, not a
  // structural guarantee this module should hardcode.
  return { action: "redirect", host: `app.${normalizedHost.replace(/^www\./, "")}` };
}
