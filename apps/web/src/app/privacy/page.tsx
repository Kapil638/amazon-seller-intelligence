import { PublicPageShell } from "@/components/public-page-shell";

// Published Privacy Notice — operator-approved final content (not a
// draft). Legal-entity, contact, and infrastructure facts below are the
// operator-confirmed facts for the public product EWise Intelligence,
// operated by Ewisepartners LLC (trading as EWISE Partners). Content
// mirrors the operator-supplied Privacy Notice text; only minor
// formatting/JSX-escaping adjustments were made — do not weaken or
// materially expand its promises. This page must remain publicly
// reachable without Cloudflare Access (see src/lib/public-routing.ts —
// `/privacy` is served identically on the marketing host and is never
// rewritten to the protected app subdomain).
export const metadata = {
  title: "Privacy Notice — EWise Intelligence",
};

export default function PrivacyPage() {
  return (
    <PublicPageShell>
      <h1 className="mb-2 text-2xl font-semibold">Privacy Notice</h1>
      <p className="mb-6 text-sm text-muted-foreground">Last updated: September 12, 2026</p>

      <div className="flex flex-col gap-6 text-sm leading-relaxed">
        <section>
          <p>
            EWise Intelligence is operated by <strong>Ewisepartners LLC</strong>, trading as{" "}
            <strong>EWISE Partners</strong>. We provide ecommerce analytics and consulting tools that help
            authorized sellers understand and improve their Amazon business and advertising performance.
          </p>
          <p className="mt-2">
            This notice explains what information EWise Intelligence processes, why we use it, and the choices
            available to you.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Information you provide</h2>
          <p className="mb-2">We may collect information that you provide directly, including:</p>
          <ul className="ml-5 list-disc">
            <li>Your name, business name, and contact information</li>
            <li>Information you enter into EWise Intelligence</li>
            <li>Product identifiers and listing information</li>
            <li>Files or reports you choose to upload</li>
            <li>Support requests and communications</li>
          </ul>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Information received from Amazon</h2>
          <p>
            EWise Intelligence accesses Amazon information only after an authorized seller or advertiser connects
            their account and grants permission.
          </p>
          <p className="mt-2 mb-2">Depending on the features enabled for your account, this information may include:</p>
          <ul className="ml-5 list-disc">
            <li>Seller account and marketplace identifiers</li>
            <li>Product listings and catalog information</li>
            <li>Orders and sales information</li>
            <li>Inventory and fulfilment information</li>
            <li>Sales and traffic reports</li>
            <li>Advertising profiles and account information</li>
            <li>Advertising campaigns, ad groups, keywords, targets, budgets, and performance reports</li>
          </ul>
          <p className="mt-2">
            Amazon Ads functionality is being introduced gradually. Advertising data is processed only when the
            account owner connects an Amazon Ads account and authorizes the requested access.
          </p>
          <p className="mt-2">We do not ask for or store your Amazon username or password.</p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">How we use information</h2>
          <p className="mb-2">We use information to:</p>
          <ul className="ml-5 list-disc">
            <li>Connect and maintain authorized Amazon integrations</li>
            <li>Present seller, product, inventory, sales, and advertising insights</li>
            <li>Produce reports, comparisons, and performance summaries</li>
            <li>Help users identify opportunities and operational issues</li>
            <li>Provide user-requested AI-assisted analysis</li>
            <li>Maintain security, diagnose errors, and prevent misuse</li>
            <li>Provide customer support</li>
            <li>Improve the reliability and usability of EWise Intelligence</li>
          </ul>
          <p className="mt-2">
            Amazon data is used only to provide and improve services for the seller or advertiser who authorized
            access.
          </p>
          <p className="mt-2">We do not use one advertiser&apos;s Amazon data to benefit another advertiser.</p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">AI-assisted features</h2>
          <p>
            When a user chooses an AI-assisted feature, relevant business facts may be sent to an AI service
            provider to generate the requested analysis or response.
          </p>
          <p className="mt-2">
            We limit the information sent to what is needed for the requested feature. We do not intentionally send
            Amazon access tokens, passwords, or other authentication credentials to AI providers.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">How we share information</h2>
          <p>We do not sell, rent, or publish Amazon data or personal information.</p>
          <p className="mt-2 mb-2">
            Information may be processed by service providers that help us operate EWise Intelligence, including
            providers of:
          </p>
          <ul className="ml-5 list-disc">
            <li>Cloud hosting</li>
            <li>Database hosting</li>
            <li>Authentication and website security</li>
            <li>Application monitoring</li>
            <li>AI-assisted analysis</li>
          </ul>
          <p className="mt-2">
            Our current infrastructure may include <strong>Amazon, Railway, Supabase, Cloudflare, and OpenAI</strong>,
            depending on the feature being used.
          </p>
          <p className="mt-2">
            These providers receive only the information needed to perform their services. We do not permit them to
            use Amazon data for their own advertising or marketing purposes.
          </p>
          <p className="mt-2">
            We may also disclose information when required to comply with applicable law or to protect the security
            and integrity of the service.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Advertising data practices</h2>
          <p className="mb-2">
            Amazon advertising data is processed only for the account that authorized EWise Intelligence.
          </p>
          <p className="mb-2">We do not:</p>
          <ul className="ml-5 list-disc">
            <li>Sell or license Amazon advertising data</li>
            <li>Publish Amazon advertising data</li>
            <li>Share advertising data between unrelated advertisers</li>
            <li>Use Amazon data for behavioral advertising or retargeting</li>
            <li>Use Amazon data to identify or profile individual consumers</li>
            <li>Combine Amazon data with unrelated third-party data without the required authorization</li>
            <li>Take advertising actions without the permissions and instructions associated with the connected account</li>
          </ul>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Data security</h2>
          <p className="mb-2">
            We use administrative and technical safeguards designed to protect information, including:
          </p>
          <ul className="ml-5 list-disc">
            <li>Encryption during transmission</li>
            <li>Encryption of sensitive stored credentials</li>
            <li>Access controls</li>
            <li>Separation of customer accounts</li>
            <li>Restricted access based on operational need</li>
            <li>Activity logging and monitoring</li>
            <li>Secure handling of Amazon authorization tokens</li>
            <li>Regular software and dependency updates</li>
          </ul>
          <p className="mt-2">
            No online service can guarantee absolute security, but we work to prevent unauthorized access,
            disclosure, alteration, or loss.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">How long we keep information</h2>
          <p>
            We retain information only while it is needed to operate the authorized service, support the connected
            account, meet security requirements, or comply with applicable obligations.
          </p>
          <p className="mt-2">
            Detailed imported Amazon data is generally removed within <strong>90 days after it is no longer needed</strong>{" "}
            for the service or after a valid deletion request, unless continued retention is required for security,
            fraud prevention, backup recovery, or applicable law.
          </p>
          <p className="mt-2">
            Amazon authorization credentials are retained only while the connection remains active or while needed
            to complete disconnection and deletion safely.
          </p>
          <p className="mt-2">
            Aggregated information that can no longer identify a seller, advertiser, account, or individual may be
            retained to improve service performance.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Disconnecting Amazon</h2>
          <p className="mb-2">
            You may disconnect your Amazon account from EWise Intelligence or revoke authorization through the
            relevant Amazon account controls.
          </p>
          <p className="mb-2">After access is revoked:</p>
          <ul className="ml-5 list-disc">
            <li>EWise Intelligence will stop requesting new data from that connection</li>
            <li>Stored authorization credentials will be disabled and deleted</li>
            <li>Associated Amazon data will be deleted according to the retention process described above</li>
          </ul>
          <p className="mt-2">
            Disconnecting EWise Intelligence does not delete information held independently by Amazon.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Your choices</h2>
          <p className="mb-2">You may contact us to:</p>
          <ul className="ml-5 list-disc">
            <li>Ask what information we hold about your account</li>
            <li>Correct inaccurate account information</li>
            <li>Request deletion of your information</li>
            <li>Request that an Amazon connection be removed</li>
            <li>Ask a privacy or security question</li>
          </ul>
          <p className="mt-2">
            We may need to verify that you are authorized to make a request for the relevant business or Amazon
            account before acting on it.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Cookies and authentication</h2>
          <p>
            EWise Intelligence uses essential cookies and similar technology to maintain authenticated sessions,
            secure access, and operate the application.
          </p>
          <p className="mt-2">We do not use Amazon data for third-party behavioral advertising.</p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Privacy and security reports</h2>
          <p className="mb-2">
            To report a privacy concern, suspected security issue, or possible misuse of Amazon data, contact:
          </p>
          <p className="mb-2">
            <strong>Ewisepartners LLC / EWISE Partners</strong>
            <br />
            Bonney Lake, WA 98391
            <br />
            Email: <strong>info@ewisepartners.com</strong>
            <br />
            Phone: <strong>(630) 261-5987</strong>
          </p>
          <p>
            For faster handling of a security report, use the subject line{" "}
            <strong>&quot;Security Report – EWise Intelligence.&quot;</strong>
          </p>
          <p className="mt-2">
            We will review reported concerns promptly and take appropriate steps to investigate, contain, and
            resolve confirmed issues.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Changes to this notice</h2>
          <p>
            We may update this notice as EWise Intelligence develops or its data practices change. The latest
            version will always be published on this page with its updated date.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-lg font-medium">Contact us</h2>
          <p className="mb-2">Questions about this Privacy Notice or EWise Intelligence can be sent to:</p>
          <p>
            <strong>Ewisepartners LLC / EWISE Partners</strong>
            <br />
            Bonney Lake, WA 98391
            <br />
            Email: <strong>info@ewisepartners.com</strong>
            <br />
            Phone: <strong>(630) 261-5987</strong>
          </p>
        </section>
      </div>
    </PublicPageShell>
  );
}
