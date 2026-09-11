import { DraftNotice, PublicPageShell } from "@/components/public-page-shell";

// pilot-deployment-ewise — DRAFT. See privacy/page.tsx's own module
// comment for the governing rule: [PENDING] markers are legal-entity,
// contact, or jurisdiction-specific facts requiring operator
// confirmation before publishing; everything else describes this
// codebase's actual, verified current behavior.
export const metadata = {
  title: "Terms of Service — EWise Amazon Seller Intelligence",
};

export default function TermsPage() {
  return (
    <PublicPageShell>
      <h1 className="mb-2 text-2xl font-semibold">Terms of Service</h1>
      <p className="mb-6 text-sm text-muted-foreground">Draft — last updated [PENDING: publish date].</p>
      <DraftNotice />

      <div className="flex flex-col gap-6 text-sm leading-relaxed">
        <section>
          <h2 className="mb-2 text-lg font-medium">The service</h2>
          <p>
            This application (&quot;the Service&quot;) is provided by <strong>[PENDING: legal operator name]</strong>{" "}
            (&quot;we&quot;, &quot;us&quot;). The Service reads Amazon Seller Central data a seller authorizes through Amazon&apos;s
            own Login with Amazon consent flow and presents deterministic analysis, profit and advertising
            calculations, and — where enabled — AI-generated explanations of that data. The Service does not place,
            modify, or cancel orders, and does not otherwise take autonomous action on a seller&apos;s Amazon
            account.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Private pilot</h2>
          <p>
            The Service is currently offered as an invitation-only private pilot. Availability, features, and
            these Terms may change materially before any general release.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Your responsibilities</h2>
          <p>
            You are responsible for the accuracy of any Amazon account you connect and for complying with
            Amazon&apos;s own Selling Partner API terms and Amazon&apos;s policies for your seller account. You
            must not use the Service to violate Amazon&apos;s policies or applicable law.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">No investment, tax, or legal advice</h2>
          <p>
            Figures shown (profit, advertising, and other calculated metrics) are deterministic calculations based
            on data available to the Service at the time. They are not financial, tax, legal, or investment advice.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Disclaimer of warranty; limitation of liability</h2>
          <p>
            <strong>[PENDING: operator to confirm the exact warranty-disclaimer and liability-limitation language
            for this jurisdiction before publishing]</strong> — do not publish this section with invented specific
            legal language.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Termination</h2>
          <p>
            Either party may end pilot participation at any time. A seller may revoke the Service&apos;s access to
            their Amazon account directly from Amazon Seller Central at any time, independent of this application.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Governing law</h2>
          <p>
            <strong>[PENDING: governing law/jurisdiction to be confirmed by the operator]</strong>.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Contact</h2>
          <p>
            Questions about these Terms: <span>[PENDING: contact email]</span>.
          </p>
        </section>

        <p className="text-xs text-muted-foreground">
          See also our <a href="/privacy" className="underline">Privacy Policy</a>.
        </p>
      </div>
    </PublicPageShell>
  );
}
