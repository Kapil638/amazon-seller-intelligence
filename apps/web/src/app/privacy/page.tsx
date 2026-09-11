import { DraftNotice, PublicPageShell } from "@/components/public-page-shell";

// pilot-deployment-ewise — legal-entity, contact, and pilot-advertiser
// facts below were confirmed directly by the operator (not invented):
// legal operator Ewisepartners LLC (trading as EWise Partners, operating
// the public product EWise Intelligence), public business location
// Bonney Lake, WA 98391, contact info@ewisepartners.com / (630)
// 261-5987, and initial pilot advertiser AJ Duran. Retention policy is
// the operator-confirmed 90-day standard.
//
// This page deliberately contains no bracketed pending-placeholder
// token — see check-legal-pages-ready.mjs's own docstring for why that
// specific marker blocks the build. A small number of items the operator
// has not yet supplied (a specific street address distinct from the
// public business location above, and a registration number / state of
// incorporation) are simply not referenced on this page rather than
// invented — do not add them without the operator's own input. See
// docs/AI_HANDOVER/20_PILOT_DEPLOYMENT_EWISE.md's "Remaining manual
// inputs" for the tracked list. Every other claim on this page is a
// factual, verified description of this codebase's actual current data
// handling — each grounded in the specific source file noted inline —
// never aspirational or assumed.
export const metadata = {
  title: "Privacy Policy — EWise Intelligence",
};

export default function PrivacyPage() {
  return (
    <PublicPageShell>
      <h1 className="mb-2 text-2xl font-semibold">Privacy Policy</h1>
      <p className="mb-6 text-sm text-muted-foreground">Last updated September 11, 2026.</p>
      <DraftNotice />

      <div className="flex flex-col gap-6 text-sm leading-relaxed">
        <section>
          <h2 className="mb-2 text-lg font-medium">Who operates this service</h2>
          <p>
            EWise Intelligence (&quot;the Service&quot;) is operated by <strong>Ewisepartners LLC</strong>, doing
            business as <strong>EWise Partners</strong>. Public business location:{" "}
            <strong>Bonney Lake, WA 98391</strong>. Contact for privacy inquiries:{" "}
            <strong>info@ewisepartners.com</strong>, telephone <strong>(630) 261-5987</strong>.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Pilot advertiser and data ownership</h2>
          <p>
            This Service is currently operated as an invitation-only private pilot. Its initial pilot advertiser is{" "}
            <strong>AJ Duran</strong>. AJ Duran owns the Amazon seller and advertising data associated with their own
            Amazon account and made available through the Service under their own authorization — the Service does
            not claim ownership of that data, and does not use it for any purpose beyond providing the Service to
            that advertiser.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Purposes of collection and processing</h2>
          <p>
            We collect and process the Amazon data described below solely to provide the Service to the advertiser
            who authorized it: to display marketplace, listing, order, inventory, sales, and (in future,
            read-only) advertising data back to that advertiser; to calculate deterministic profit, advertising,
            and listing-quality metrics from it; and, only where an advertiser actively uses an AI-assisted
            analysis feature, to generate natural-language explanations of it. We do not use Amazon data for
            advertising to advertisers, for profiling, or for any purpose unrelated to providing the Service to the
            advertiser it belongs to.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">What Amazon data we access</h2>
          <p>
            Amazon access requires the advertiser&apos;s own explicit authorization, granted through Amazon&apos;s
            Selling Partner API (SP-API) Login with Amazon consent flow. Once authorized, this application reads:
            marketplace participation, seller-owned product listings, orders and order line items, FBA inventory
            levels, and Sales &amp; Traffic business reports. This application does not place, modify, or cancel
            Amazon orders, and does not otherwise write to an advertiser&apos;s Amazon account.
          </p>
          <p className="mt-2">
            Amazon Ads data: <strong>we do not yet collect any Amazon Advertising data</strong> — Amazon Ads API
            integration has not been built and is not yet authorized by Amazon. When it is built, it will initially
            be <strong>read-only</strong>: the Service will read advertising performance data an advertiser
            authorizes, and will not place, modify, or manage advertising campaigns on an advertiser&apos;s behalf.
            This policy will be updated, and reviewed with advertisers before rollout, if and when that changes.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">How we store Amazon credentials</h2>
          <p>
            We never store an advertiser&apos;s Amazon password. Authorization uses Amazon&apos;s own Login with
            Amazon OAuth flow; we receive and store only an opaque, revocable refresh-token reference issued by
            Amazon, kept separate from ordinary business data and never exposed to this application&apos;s own AI
            features, logs, or API responses. That reference is encrypted at the application layer (AES-256-GCM,
            with a unique nonce per value and versioned encryption keys) before it is stored, in addition to our
            database provider&apos;s own infrastructure-level encryption at rest.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Sub-processors and infrastructure</h2>
          <p className="mb-2">
            The following infrastructure processors support the Service. None of them receives an
            advertiser&apos;s Amazon password, and none is authorized to act on an advertiser&apos;s Amazon account
            independently of the Service.
          </p>
          <ul className="ml-5 list-disc">
            <li>
              <strong>Supabase</strong> — hosts our PostgreSQL database (seller/advertiser connection metadata,
              encrypted Amazon credential references, and ingested Amazon business data).
            </li>
            <li>
              <strong>Railway</strong> — hosts our application servers and background data-synchronization
              processes.
            </li>
            <li>
              <strong>Cloudflare</strong> — provides DNS, TLS/encryption in transit, and access control for this
              domain.
            </li>
            <li>
              <strong>OpenAI</strong> — used only when an advertiser actively uses an AI-assisted analysis feature
              (for example, the Service&apos;s Copilot explanations and summaries). In that case only, the specific,
              already-retrieved, authorized Amazon business facts relevant to that request (for example order or
              listing data) are sent to OpenAI to generate the response. OpenAI is not used for any other purpose,
              and never receives Amazon account credentials, tokens, or unrelated advertiser data.
            </li>
          </ul>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Retention and deletion</h2>
          <p>
            EWise Intelligence retains authorized advertiser data while the connection or pilot service remains
            active. Following authorization revocation or service termination, the data will be deleted or
            irreversibly anonymized within 90 days unless a shorter period is required by Amazon policy or a
            longer period is legally required. An advertiser may revoke the Service&apos;s access to their Amazon
            account at any time (see &quot;Revoking access&quot; below) and may separately request deletion of
            their data at any time by contacting us at the address above.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Revoking access</h2>
          <p>
            An advertiser may revoke this application&apos;s access to their Amazon account at any time directly
            from Amazon Seller Central, independent of this application. Revoking access there stops all further
            data collection immediately.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">No sale of data; no cross-advertiser disclosure</h2>
          <p>
            We do not sell advertiser data. We do not share one advertiser&apos;s Amazon seller or advertising data
            with another advertiser, seller, or any unrelated third party.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Access control</h2>
          <p>
            This is a private pilot: access to the application is limited to invited advertisers. The application is
            designed to sit behind an authenticated access-control layer (Cloudflare Access) requiring sign-in
            before any protected page or API route can be reached — confirming this protection is actually turned
            on for the live deployment is one of the operator&apos;s own pre-launch verification steps (see
            docs/AI_HANDOVER/20_PILOT_DEPLOYMENT_EWISE.md), and this page will be updated if that design changes.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Contact</h2>
          <p>
            Questions about this policy: <strong>info@ewisepartners.com</strong>, telephone{" "}
            <strong>(630) 261-5987</strong>.
          </p>
        </section>
      </div>
    </PublicPageShell>
  );
}
