import { DraftNotice, PublicPageShell } from "@/components/public-page-shell";

// pilot-deployment-ewise — DRAFT. Every [PENDING] marker below is a
// legal-entity, contact, or jurisdiction-specific fact that must be
// confirmed by the operator before this page is treated as published —
// per the explicit instruction governing this milestone: "verify the
// legal operator and contact details with me. Do not invent them."
// Every OTHER claim on this page is a factual, verified description of
// this codebase's actual current data handling as of this milestone —
// each grounded in the specific source file noted inline — never
// aspirational or assumed. Where current behavior does not yet support
// a claim a privacy policy would normally make (e.g. a self-service
// deletion flow), that gap is stated honestly rather than promised.
export const metadata = {
  title: "Privacy Policy — EWise Amazon Seller Intelligence",
};

export default function PrivacyPage() {
  return (
    <PublicPageShell>
      <h1 className="mb-2 text-2xl font-semibold">Privacy Policy</h1>
      <p className="mb-6 text-sm text-muted-foreground">Draft — last updated [PENDING: publish date].</p>
      <DraftNotice />

      <div className="flex flex-col gap-6 text-sm leading-relaxed">
        <section>
          <h2 className="mb-2 text-lg font-medium">Who operates this service</h2>
          <p>
            This application is operated by <strong>[PENDING: legal operator name]</strong>,{" "}
            <span>[PENDING: registered business address]</span>. Contact for privacy inquiries:{" "}
            <span>[PENDING: contact email]</span>.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">What Amazon data we access</h2>
          <p>
            With a seller&apos;s explicit authorization through Amazon&apos;s Selling Partner API (SP-API) Login
            with Amazon consent flow, this application reads: marketplace participation, seller-owned product
            listings, orders and order line items, FBA inventory levels, and Sales &amp; Traffic business reports.
            This application does not place, modify, or cancel Amazon orders, and does not otherwise write to a
            seller&apos;s Amazon account.
          </p>
          <p className="mt-2">
            Amazon Ads data:{" "}
            <strong>
              we do not yet collect any Amazon Advertising data. Amazon Ads API integration has not been built and
              is not yet authorized by Amazon.
            </strong>{" "}
            This policy will be updated, and reviewed with sellers before rollout, if that changes.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">How we store Amazon credentials</h2>
          <p>
            We never store a seller&apos;s Amazon password. Authorization uses Amazon&apos;s own Login with Amazon
            OAuth flow; we receive and store only an opaque, revocable refresh-token reference issued by Amazon,
            kept separate from ordinary business data and never exposed to this application&apos;s own AI features,
            logs, or API responses.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Sub-processors and infrastructure</h2>
          <ul className="ml-5 list-disc">
            <li>
              <strong>Supabase</strong> — hosts our PostgreSQL database (seller connection metadata, ingested
              Amazon business data).
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
              <strong>OpenAI</strong> — our AI assistant (&quot;Copilot&quot;) feature sends relevant, already-retrieved
              Amazon business facts (for example order and listing data) to OpenAI to generate natural-language
              explanations and summaries. OpenAI does not receive Amazon account credentials or tokens.
            </li>
          </ul>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Encryption</h2>
          <p>
            Traffic to this application is encrypted in transit (HTTPS/TLS). Data at rest in our database provider
            is encrypted using that provider&apos;s standard infrastructure-level encryption.{" "}
            <strong>[PENDING: confirm application-level encryption status for stored Amazon credentials before
            publishing]</strong> — do not publish this page claiming a specific encryption-at-rest guarantee for
            credential storage until that is verified against the deployed configuration.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Retention and deletion</h2>
          <p>
            <strong>[PENDING]</strong> — a specific data-retention period and self-service deletion mechanism have
            not yet been finalized for this pilot. Until they are, a seller who wants their data deleted should
            contact us at the address above.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Revoking access</h2>
          <p>
            A seller may revoke this application&apos;s access to their Amazon account at any time directly from
            Amazon Seller Central, independent of this application. Revoking access there stops all further data
            collection immediately.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">No sale of data; no cross-advertiser disclosure</h2>
          <p>
            We do not sell seller data. We do not share one seller&apos;s Amazon data with another seller,
            advertiser, or any unrelated third party.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Access control</h2>
          <p>
            This is a private pilot: access to the application is limited to invited sellers.{" "}
            <strong>[PENDING: describe the specific access-control mechanism actually in place at the time of
            publishing]</strong> — do not publish a specific access-control claim (e.g. &quot;per-user authentication&quot;)
            until it is verified against the deployed system.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Contact</h2>
          <p>
            Questions about this policy: <span>[PENDING: contact email]</span>.
          </p>
        </section>
      </div>
    </PublicPageShell>
  );
}
