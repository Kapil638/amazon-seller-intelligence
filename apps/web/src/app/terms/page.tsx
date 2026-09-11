import { DraftNotice, PublicPageShell } from "@/components/public-page-shell";

// pilot-deployment-ewise — see privacy/page.tsx's own module comment for
// the governing rule and rationale: this page deliberately contains no
// bracketed pending-placeholder token (see check-legal-pages-ready.mjs),
// but the "Disclaimer of warranty; limitation of liability" and "Governing
// law" sections below intentionally do not state specific liability-cap
// or jurisdiction language — that is real legal drafting this document
// must not invent. Both sections instead honestly state that this
// specific language is not yet finalized, rather than fabricating it or
// silently omitting the topic. See docs/AI_HANDOVER/
// 20_PILOT_DEPLOYMENT_EWISE.md's "Remaining manual inputs" for the
// tracked list of what still needs the operator's input.
export const metadata = {
  title: "Terms of Service — EWise Intelligence",
};

export default function TermsPage() {
  return (
    <PublicPageShell>
      <h1 className="mb-2 text-2xl font-semibold">Terms of Service</h1>
      <p className="mb-6 text-sm text-muted-foreground">Last updated September 11, 2026.</p>
      <DraftNotice />

      <div className="flex flex-col gap-6 text-sm leading-relaxed">
        <section>
          <h2 className="mb-2 text-lg font-medium">The service</h2>
          <p>
            EWise Intelligence (&quot;the Service&quot;) is provided by <strong>Ewisepartners LLC</strong>, doing
            business as <strong>EWise Partners</strong> (&quot;we&quot;, &quot;us&quot;), of Bonney Lake, WA 98391.
            The Service reads Amazon Seller Central data an advertiser authorizes through Amazon&apos;s own Login
            with Amazon consent flow and presents deterministic analysis, profit and advertising calculations, and —
            where enabled — AI-generated explanations of that data. The Service does not place, modify, or cancel
            orders, and does not otherwise take autonomous action on an advertiser&apos;s Amazon account.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Private pilot and initial advertiser</h2>
          <p>
            The Service is currently offered as an invitation-only private pilot. Availability, features, and these
            Terms may change materially before any general release. The Service&apos;s initial pilot advertiser is{" "}
            <strong>AJ Duran</strong>. Each advertiser retains ownership of their own Amazon seller and advertising
            data made available through the Service; the Service does not claim ownership of it and does not
            disclose it to any other advertiser.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Amazon Advertising (Ads) functionality</h2>
          <p>
            Amazon Ads API integration is not yet built. When it is introduced, it will initially be{" "}
            <strong>read-only</strong>: the Service will read advertising performance data an advertiser authorizes
            and will not place, modify, or manage advertising campaigns on an advertiser&apos;s behalf. These Terms
            will be updated, and reviewed with advertisers, before any write-capable Ads functionality is
            introduced.
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
            The Service is provided on an as-is basis during this early private pilot. A complete, jurisdiction-
            specific disclaimer of warranties and limitation-of-liability clause has not yet been finalized and will
            be published here, and reviewed with advertisers, before the Service is offered more broadly. This
            paragraph is not a substitute for that clause and does not itself limit either party&apos;s liability.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Termination</h2>
          <p>
            Either party may end pilot participation at any time. An advertiser may revoke the Service&apos;s access
            to their Amazon account directly from Amazon Seller Central at any time, independent of this
            application. Data retention after termination follows the Privacy Policy&apos;s own &quot;Retention and
            deletion&quot; section, including our ability to retain data as needed to meet legitimate legal,
            accounting, or audit obligations.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Governing law</h2>
          <p>
            A specific governing-law and dispute-resolution clause has not yet been finalized for this pilot and
            will be published here before the Service is offered more broadly. Until then, any dispute is addressed
            directly between Ewisepartners LLC and the advertiser.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Contact</h2>
          <p>
            Questions about these Terms: <strong>info@ewisepartners.com</strong>, telephone{" "}
            <strong>(630) 261-5987</strong>.
          </p>
        </section>

        <p className="text-xs text-muted-foreground">
          See also our <a href="/privacy" className="underline">Privacy Policy</a>.
        </p>
      </div>
    </PublicPageShell>
  );
}
