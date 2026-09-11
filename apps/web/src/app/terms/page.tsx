import { DraftNotice, PublicPageShell } from "@/components/public-page-shell";

// pilot-deployment-ewise — see privacy/page.tsx's own module comment for
// the governing rule and rationale. Warranty disclaimer, third-party-
// services, liability cap, Washington governing law, 30-day good-faith
// dispute discussion, and severability provisions below are the
// operator-approved final language for this pilot. This page
// deliberately still does not state a street address beyond the public
// business location, a registration number, or a state of incorporation
// — none were supplied, and none are invented. See docs/AI_HANDOVER/
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
          <h2 className="mb-2 text-lg font-medium">Third-party services</h2>
          <p>
            The Service depends on third-party services outside our control, including Amazon&apos;s Selling
            Partner API and (when introduced) Amazon Ads API, Supabase, Railway, Cloudflare, and OpenAI (see the
            Privacy Policy&apos;s &quot;Sub-processors and infrastructure&quot; section). We are not responsible for
            outages, changes, errors, or policy changes made by those third parties, though we will make reasonable
            efforts to keep the Service working with them and to notify advertisers of material changes.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Disclaimer of warranty</h2>
          <p>
            THE SERVICE IS PROVIDED &quot;AS IS&quot; AND &quot;AS AVAILABLE,&quot; WITHOUT WARRANTY OF ANY KIND,
            EXPRESS OR IMPLIED, INCLUDING WITHOUT LIMITATION THE IMPLIED WARRANTIES OF MERCHANTABILITY, FITNESS FOR
            A PARTICULAR PURPOSE, AND NON-INFRINGEMENT. We do not warrant that the Service will be uninterrupted,
            error-free, or free of inaccuracies (including inaccuracies originating from Amazon&apos;s or another
            third party&apos;s own data or systems), or that any calculated figure will be correct for any
            particular purpose.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Limitation of liability</h2>
          <p>
            To the maximum extent permitted by law, neither party will be liable to the other for any indirect,
            incidental, special, consequential, or punitive damages, or for any loss of profits, revenue, data, or
            business opportunity, arising out of or relating to the Service or these Terms. Each party&apos;s total
            aggregate liability arising out of or relating to the Service or these Terms will not exceed the
            greater of (a) the fees the advertiser paid us in the 12 months preceding the claim, or (b) one hundred
            US dollars (US $100). This limitation does not apply to liability that cannot legally be excluded or
            limited, or to liability arising from fraud, willful misconduct, or gross negligence.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Termination</h2>
          <p>
            Either party may end pilot participation at any time. An advertiser may revoke the Service&apos;s access
            to their Amazon account directly from Amazon Seller Central at any time, independent of this
            application. Data retention after termination follows the Privacy Policy&apos;s own &quot;Retention and
            deletion&quot; section.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Governing law and dispute resolution</h2>
          <p>
            These Terms are governed by the laws of the State of Washington, without regard to its conflict-of-laws
            principles. Before filing any claim relating to the Service or these Terms, both parties agree to
            attempt in good faith to resolve the dispute through written discussion for at least 30 days. If the
            dispute is not resolved within that period, it will be brought exclusively in the state or federal
            courts located in Washington State, and each party consents to the personal jurisdiction of those
            courts.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Severability</h2>
          <p>
            If any provision of these Terms is held invalid or unenforceable, that provision will be limited or
            eliminated to the minimum extent necessary, and the remaining provisions will remain in full force and
            effect.
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
