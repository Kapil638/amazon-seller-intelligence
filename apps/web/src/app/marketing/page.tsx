import Link from "next/link";

import { PublicPageShell } from "@/components/public-page-shell";

// pilot-deployment-ewise — served at the bare marketing domain's root
// (`ewiseintelligence.com`) via middleware.ts's rewrite, not linked to
// directly from the app. Product description only, drawn from this
// codebase's own established mission statement (CLAUDE.md) — no legal,
// operator, or contact claims live here; those belong on /privacy and
// /terms, where they are explicitly marked pending confirmation.
export const metadata = {
  title: "EWise Amazon Seller Intelligence",
  description:
    "Amazon Seller Intelligence for EWise — marketplace listing insight, profit and advertising analysis, and Amazon-owned seller operational data in one place.",
};

export default function MarketingPage() {
  return (
    <PublicPageShell>
      <div className="flex flex-col gap-6">
        <h1 className="text-3xl font-semibold">Amazon Seller Intelligence</h1>
        <p className="text-muted-foreground">
          EWise&apos;s Amazon Seller Intelligence platform helps sellers understand their marketplace listings, profit,
          advertising performance, and their own Amazon-owned operational data — inventory, orders, and sales &amp;
          traffic — in one connected view.
        </p>
        <p className="text-muted-foreground">
          This is not an autonomous Amazon bot. It never writes to Amazon on a seller&apos;s behalf, and it never
          invents business figures — every number shown is either observed directly from Amazon&apos;s own APIs or
          calculated deterministically from those observations.
        </p>
        <div className="flex flex-wrap gap-3 pt-2">
          <a
            href="https://app.ewiseintelligence.com"
            className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
          >
            Go to the app
          </a>
          <Link href="/privacy" className="rounded-md border border-input px-4 py-2 text-sm font-medium">
            Privacy policy
          </Link>
          <Link href="/terms" className="rounded-md border border-input px-4 py-2 text-sm font-medium">
            Terms
          </Link>
        </div>
        <p className="pt-6 text-sm text-muted-foreground">
          Access to the application is currently limited to a private pilot. Contact details:{" "}
          <span className="font-mono">[PENDING — operator contact email to be confirmed]</span>.
        </p>
      </div>
    </PublicPageShell>
  );
}
