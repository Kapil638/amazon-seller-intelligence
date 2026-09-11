import Link from "next/link";

import { PublicPageShell } from "@/components/public-page-shell";

// pilot-deployment-ewise — served at the bare marketing domain's root
// (`ewiseintelligence.com`) via proxy.ts's rewrite, not linked to
// directly from the app. Product description drawn from this
// codebase's own established mission statement (CLAUDE.md), plus the
// operator-confirmed legal-entity/contact facts also used on /privacy
// and /terms — see privacy/page.tsx's own module comment for the
// governing rule on what may and may not be stated here.
export const metadata = {
  title: "EWise Intelligence",
  description:
    "EWise Intelligence — marketplace listing insight, profit and advertising analysis, and Amazon-owned seller operational data in one place.",
};

export default function MarketingPage() {
  return (
    <PublicPageShell>
      <div className="flex flex-col gap-6">
        <h1 className="text-3xl font-semibold">EWise Intelligence</h1>
        <p className="text-muted-foreground">
          EWise Intelligence helps sellers and advertisers understand their marketplace listings, profit,
          advertising performance, and their own Amazon-owned operational data — inventory, orders, and sales &amp;
          traffic — in one connected view.
        </p>
        <p className="text-muted-foreground">
          This is not an autonomous Amazon bot. It never writes to Amazon on an advertiser&apos;s behalf, and it
          never invents business figures — every number shown is either observed directly from Amazon&apos;s own
          APIs or calculated deterministically from those observations.
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
          EWise Intelligence is operated by Ewisepartners LLC, doing business as EWise Partners, of Bonney Lake, WA
          98391. Access to the application is currently limited to a private pilot. Contact:{" "}
          <span className="font-mono">info@ewisepartners.com</span>, telephone{" "}
          <span className="font-mono">(630) 261-5987</span>.
        </p>
      </div>
    </PublicPageShell>
  );
}
