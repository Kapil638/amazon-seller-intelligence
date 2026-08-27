"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2 } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState, PageHeader, Panel, Section } from "@/components/ui/layout";
import { AmazonConnectionError, authorizeAmazonConnection, fetchAmazonConnection, testAmazonConnection } from "@/lib/api";
import type {
  AmazonConnectionLifecycleStatus,
  AmazonConnectionOverview,
  AmazonConnectionStatus,
  AmazonConnectionTestResult,
  AmazonIngestionRunStatus,
  AmazonSellerMarketplace,
} from "@/lib/types";

const AUTHORIZE_START_FAILED = "Unable to start Amazon connection. Please try again.";
const AUTHORIZE_APP_NOT_CONFIGURED = "Amazon application is not configured on the API.";

const SECRET_ACCESS_FAILED = "secret_access_failed";

type AmazonReturnNotice = "success" | "denied" | "error";

function canStartAuthorization(
  status: AmazonConnectionLifecycleStatus,
  lastErrorCode: string | null,
): boolean {
  if (lastErrorCode === SECRET_ACCESS_FAILED) {
    return true;
  }
  return (
    status === "not_connected" ||
    status === "pending_authorization" ||
    status === "revoked" ||
    status === "error"
  );
}

function canValidateGrant(
  status: AmazonConnectionLifecycleStatus,
  lastErrorCode: string | null,
): boolean {
  return status === "pending_validation" && lastErrorCode !== SECRET_ACCESS_FAILED;
}

function authorizeStartErrorMessage(caught: unknown): string {
  if (caught instanceof AmazonConnectionError) {
    if (/amazon application is not configured/i.test(caught.message)) {
      return AUTHORIZE_APP_NOT_CONFIGURED;
    }
  }
  return AUTHORIZE_START_FAILED;
}

function persistedStatusCode(status: AmazonConnectionLifecycleStatus): string {
  return status.toUpperCase();
}

function persistedStatusLabel(status: AmazonConnectionLifecycleStatus): string {
  if (status === "connected") {
    return "Connected";
  }
  if (status === "pending_authorization") {
    return "Pending authorization";
  }
  if (status === "pending_validation") {
    return "Pending validation";
  }
  if (status === "degraded") {
    return "Degraded";
  }
  if (status === "revoked") {
    return "Revoked";
  }
  if (status === "error") {
    return "Error";
  }
  return "Not connected";
}

function persistedDotClass(status: AmazonConnectionLifecycleStatus): string {
  if (status === "connected") {
    return "bg-emerald-500";
  }
  if (status === "pending_authorization" || status === "pending_validation" || status === "degraded") {
    return "bg-amber-500";
  }
  if (status === "error" || status === "revoked") {
    return "bg-destructive";
  }
  return "bg-muted-foreground/50";
}

type AttentionState = { label: string; description: string; destructive: boolean };

/**
 * Renders only when the backend has actually returned a specific, known
 * error condition — never a generic "any error means total failure" banner.
 */
function attentionState(
  status: AmazonConnectionLifecycleStatus,
  lastErrorCode: string | null,
): AttentionState | null {
  if (status === "error" && lastErrorCode === "requires_reauth") {
    return {
      label: "Reauthorization required",
      description:
        "Amazon reports this authorization is no longer valid. Connect Amazon again to restore access.",
      destructive: true,
    };
  }
  if (status === "error" && lastErrorCode === "ownership_conflict") {
    return {
      label: "Needs attention",
      description:
        "This Amazon seller account is already linked to a different ASI organization, so its marketplace data could not be synchronized here.",
      destructive: true,
    };
  }
  if (status === "error" && lastErrorCode === "identity_missing") {
    return {
      label: "Reauthorization required",
      description:
        "Amazon seller identity is not available for this connection. Connect Amazon again to restore access.",
      destructive: true,
    };
  }
  if (status === "error" && lastErrorCode === "identity_conflict") {
    return {
      label: "Needs attention",
      description: "Amazon seller identity could not be confirmed for this connection.",
      destructive: true,
    };
  }
  if (status === "degraded") {
    return {
      label: "Needs attention",
      description: "Amazon SP-API was temporarily unavailable during the last check. Try Test connection again shortly.",
      destructive: false,
    };
  }
  return null;
}

function amazonReturnNotice(): AmazonReturnNotice | null {
  if (typeof window === "undefined") {
    return null;
  }
  const value = new URLSearchParams(window.location.search).get("amazon");
  if (value === "success" || value === "denied" || value === "error") {
    return value;
  }
  return null;
}

function sandboxStatusLabel(status: AmazonConnectionStatus | null): string {
  if (status === "CONNECTED") {
    return "CONNECTED";
  }
  if (status === "FAILED") {
    return "FAILED";
  }
  if (status === "NOT_CONNECTED") {
    return "NOT_CONNECTED";
  }
  return "Not tested";
}

function sandboxDotClass(status: AmazonConnectionStatus | null): string {
  if (status === "CONNECTED") {
    return "bg-emerald-500";
  }
  if (status === "FAILED") {
    return "bg-destructive";
  }
  return "bg-muted-foreground/50";
}

function ingestionStatusLabel(status: AmazonIngestionRunStatus): string {
  if (status === "succeeded") {
    return "Completed";
  }
  if (status === "partial") {
    return "Completed with warnings";
  }
  if (status === "started") {
    return "In progress";
  }
  return "Failed";
}

function ingestionDotClass(status: AmazonIngestionRunStatus): string {
  if (status === "succeeded") {
    return "bg-emerald-500";
  }
  if (status === "partial" || status === "started") {
    return "bg-amber-500";
  }
  return "bg-destructive";
}

/**
 * Fixed, reviewed copy only — the backend limits `failure_class` to a small
 * set of known reason codes (never a raw exception message), so this map
 * covers all of them. Anything unrecognized still gets a generic, safe
 * sentence rather than the raw code or no message at all.
 */
function failureClassLabel(code: string | null): string {
  const known: Record<string, string> = {
    ownership_conflict:
      "This Amazon seller account is already linked to a different ASI organization.",
    database_failure:
      "A temporary internal error prevented saving marketplace data. This will retry on the next Test connection.",
    identity_missing: "Amazon did not return a seller identifier during this synchronization attempt.",
    malformed_participations: "Amazon returned marketplace data ASI could not parse.",
    empty_snapshot: "Amazon returned no marketplace data to synchronize this time.",
  };
  if (code && known[code]) {
    return known[code];
  }
  return "Marketplace synchronization did not complete successfully.";
}

function formatTimestamp(value: string | null | undefined, empty = "Not recorded"): string {
  if (!value) {
    return empty;
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
}

function formatRegion(region: string): string {
  const key = region.trim().toLowerCase();
  if (key === "na" || key === "us") {
    return "NA (US)";
  }
  return region.trim().toUpperCase() || region;
}

function formatEnvironment(environment: string): string {
  if (environment === "SANDBOX") {
    return "Sandbox";
  }
  if (environment === "PRODUCTION") {
    return "Production";
  }
  return environment;
}

function marketplaceDisplayName(marketplace: AmazonSellerMarketplace): string {
  return marketplace.name || marketplace.domain_name || marketplace.marketplace_id;
}

type SynchronizationEvidence = {
  /** Authoritative proof at least one synchronization ever succeeded — from
   * `last_successful_sync_at` alone, never from marketplace row presence. */
  everSucceeded: boolean;
  isRunning: boolean;
  isFailed: boolean;
  /** Connected-state summary sentence for the top card. */
  summary: string;
  /** Note shown above the marketplace list only when the cards being shown
   * are last-known data from a run other than a currently-succeeding one. */
  marketplacesSectionNote: string | null;
  /** Description for the empty state when there are no marketplace rows at all. */
  emptyStateDescription: string;
  futureConnectionsStatus: string;
};

/**
 * The single source of truth for every synchronization claim on this page.
 * Marketplace presence (`marketplaces.length`) never appears here — it only
 * ever controls whether cards or the empty state render, per the required
 * evidence semantics: presence is not proof of a successful recent sync.
 */
function synchronizationEvidence(
  overview: AmazonConnectionOverview | null,
  connectionStatus: AmazonConnectionLifecycleStatus,
): SynchronizationEvidence {
  const everSucceeded = Boolean(overview?.last_successful_sync_at);
  const latestStatus = overview?.latest_ingestion?.status ?? null;
  const isRunning = latestStatus === "started";
  const isFailed = latestStatus === "failed" || latestStatus === "timed_out";
  const lastSyncedAt = formatTimestamp(overview?.last_successful_sync_at ?? null);
  const failureReason = failureClassLabel(overview?.latest_ingestion?.failure_class ?? null);
  const connected = connectionStatus === "connected";

  let summary = "This is ASI’s stored connection record.";
  let marketplacesSectionNote: string | null = null;
  let emptyStateDescription =
    "Marketplace participation appears here once the seller grant is authorized and validated.";

  if (connected) {
    if (isRunning) {
      summary = everSucceeded
        ? `Seller authorization is validated. A new marketplace synchronization is in progress. Showing marketplace data from the last successful synchronization at ${lastSyncedAt}.`
        : "Seller authorization is validated. Marketplace synchronization is in progress for the first time.";
      marketplacesSectionNote = everSucceeded
        ? `Synchronization in progress. Showing last-known marketplace data from ${lastSyncedAt}.`
        : null;
      emptyStateDescription = "Synchronization is in progress for the first time.";
    } else if (isFailed) {
      summary = everSucceeded
        ? `Seller authorization is validated. Marketplace data was last synchronized successfully at ${lastSyncedAt}. The most recent synchronization attempt failed: ${failureReason}`
        : `Seller authorization is validated. Marketplace participation has never synchronized successfully. The most recent attempt failed: ${failureReason}`;
      marketplacesSectionNote = everSucceeded
        ? `The most recent synchronization attempt failed. Showing last-known marketplace data from ${lastSyncedAt}.`
        : null;
      emptyStateDescription = everSucceeded
        ? summary
        : `Seller authorization is validated, but marketplace participation has never synchronized successfully. The most recent attempt failed: ${failureReason}`;
    } else if (everSucceeded) {
      summary = `Seller authorization is validated. Marketplace participation synchronized successfully at ${lastSyncedAt}. Listings, orders, inventory, financials, and advertising data are not yet ingested.`;
      emptyStateDescription = summary;
    } else {
      summary =
        "Seller authorization is validated. Marketplace participation has never synchronized successfully — click Test connection below to synchronize it.";
      emptyStateDescription =
        "Seller authorization is validated, but marketplace participation has never synchronized successfully. Click Test connection below.";
    }
  }

  return {
    everSucceeded,
    isRunning,
    isFailed,
    summary,
    marketplacesSectionNote,
    emptyStateDescription,
    futureConnectionsStatus: everSucceeded ? "Marketplace participation synchronized" : "Seller ingest not started",
  };
}

function MetaRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[11rem_minmax(0,1fr)] items-baseline gap-3 py-2.5">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="text-sm font-medium text-foreground">{value}</dd>
    </div>
  );
}

function MarketplaceCard({ marketplace }: { marketplace: AmazonSellerMarketplace }) {
  return (
    <Panel className="p-5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-base font-semibold">{marketplaceDisplayName(marketplace)}</p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {marketplace.country_code ?? "Unknown country"}
            {marketplace.domain_name ? ` · ${marketplace.domain_name}` : ""}
          </p>
        </div>
        <div className="flex flex-wrap justify-end gap-1.5">
          {!marketplace.is_participating ? (
            <Badge variant="secondary">Not participating</Badge>
          ) : null}
          {marketplace.has_suspended_listings ? (
            <Badge className="border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400">
              Suspended listings
            </Badge>
          ) : null}
          {!marketplace.is_active ? <Badge variant="outline">No longer reported</Badge> : null}
        </div>
      </div>
      <p className="mt-3 text-xs text-muted-foreground">
        Last seen {formatTimestamp(marketplace.last_seen_at)}
      </p>
    </Panel>
  );
}

export function AmazonConnection() {
  const [overview, setOverview] = useState<AmazonConnectionOverview | null>(null);
  const [lastTest, setLastTest] = useState<AmazonConnectionTestResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [testing, setTesting] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [returnNotice, setReturnNotice] = useState<AmazonReturnNotice | null>(null);

  const loadOverview = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const next = await fetchAmazonConnection();
      setOverview(next);
    } catch (caught) {
      const message =
        caught instanceof AmazonConnectionError
          ? caught.message
          : "Amazon Connection could not load connection status.";
      setError(message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadOverview();
  }, [loadOverview]);

  useEffect(() => {
    setReturnNotice(amazonReturnNotice());
  }, []);

  const connectionStatus = overview?.connection_status ?? "not_connected";
  const persisted = overview?.persisted ?? false;
  const provider = overview?.provider ?? "SP_API";
  const environment = overview?.environment ?? "PRODUCTION";
  const region = overview?.region ?? "na";
  const marketplace = overview?.marketplace ?? "amazon.com";
  const application = overview?.application ?? "EWise";
  const lastErrorCode = overview?.last_error_code ?? null;
  const sandboxStatus = lastTest?.status ?? null;
  const waitingForAmazon = connectionStatus === "pending_authorization";
  const waitingForValidation = connectionStatus === "pending_validation";
  const secretLost = lastErrorCode === SECRET_ACCESS_FAILED;
  const showConnect = !loading && canStartAuthorization(connectionStatus, lastErrorCode);
  const showValidate = !loading && canValidateGrant(connectionStatus, lastErrorCode);
  const attention = attentionState(connectionStatus, lastErrorCode);

  // 12B.2B fields are additive; a response from before that deploy (or a
  // stale cached one) simply omits them, so every access below defaults
  // safely rather than assuming the fields exist.
  const sellerMarketplaces = overview?.marketplaces ?? [];
  const latestIngestion = overview?.latest_ingestion ?? null;
  const sellerAccountDisplayName = overview?.seller_account_display_name ?? null;
  const sync = useMemo(
    () => synchronizationEvidence(overview, connectionStatus),
    [overview, connectionStatus],
  );

  const sellerAccountLabel = useMemo(() => {
    if (sellerAccountDisplayName) {
      return sellerAccountDisplayName;
    }
    if (overview?.selling_partner_id) {
      return overview.selling_partner_id;
    }
    return null;
  }, [sellerAccountDisplayName, overview?.selling_partner_id]);

  const persistedHint = useMemo(() => {
    if (loading) {
      return null;
    }
    if (!overview) {
      return null;
    }
    if (!persisted) {
      return "No saved Amazon connection. Showing environment defaults. A sandbox test does not authorize a seller account.";
    }
    if (secretLost) {
      return "Amazon authorization completed, but ASI could not read the stored grant. Connect Amazon again, Allow in Seller Central, then click Validate connection.";
    }
    if (connectionStatus === "pending_authorization") {
      return "Waiting for Amazon authorization. Finish consent in Seller Central. ASI will not show Connected until Amazon returns and the grant is validated.";
    }
    if (connectionStatus === "pending_validation") {
      return "Amazon authorization completed. Click Validate connection to confirm the seller grant. ASI will not show Connected until that handshake succeeds.";
    }
    if (connectionStatus === "connected") {
      return sync.summary;
    }
    return "This is ASI’s stored connection record.";
  }, [loading, overview, persisted, connectionStatus, secretLost, sync]);

  const sandboxHint = useMemo(() => {
    if (lastTest?.message) {
      return lastTest.message;
    }
    if (waitingForValidation && !secretLost) {
      return "Validate connection confirms the authorized seller grant with Amazon Sellers and synchronizes connected marketplaces. It does not ingest listings, orders, or ads.";
    }
    if (lastTest?.status === "CONNECTED") {
      return "Grant validation succeeded.";
    }
    if (overview && !overview.credentials_configured) {
      return "Sandbox credentials are not configured on the API. Connection testing stays local to this page.";
    }
    return "Test connection performs a fresh live validation against Amazon and may refresh which marketplaces appear below. It does not ingest listings, orders, or ads.";
  }, [lastTest, overview, waitingForValidation, secretLost]);

  async function onTestConnection() {
    setTesting(true);
    setError(null);
    try {
      const result = await testAmazonConnection();
      setLastTest(result);
      const next = await fetchAmazonConnection();
      setOverview(next);
    } catch (caught) {
      const message =
        caught instanceof AmazonConnectionError
          ? caught.message
          : "Amazon Connection could not complete this test.";
      setError(message);
    } finally {
      setTesting(false);
    }
  }

  async function onConnectAmazon() {
    setConnecting(true);
    setError(null);
    try {
      const started = await authorizeAmazonConnection("PRODUCTION");
      window.location.assign(started.authorization_url);
    } catch (caught) {
      setError(authorizeStartErrorMessage(caught));
      setConnecting(false);
    }
  }

  return (
    <div className="space-y-8">
      <PageHeader
        title="Amazon Seller Connection (Beta)"
        description="Manage Amazon seller-data connectivity without changing marketplace intelligence. Rainforest remains the public marketplace source. SP-API is for seller-owned data."
      />

      {error ? (
        <Alert variant="destructive">
          <AlertTitle>Connection error</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}

      {returnNotice === "success" ? (
        <Alert>
          <AlertTitle>Amazon authorization</AlertTitle>
          <AlertDescription>
            Amazon authorization completed. Click Validate connection to confirm the seller grant.
          </AlertDescription>
        </Alert>
      ) : null}
      {returnNotice === "denied" ? (
        <Alert>
          <AlertTitle>Amazon authorization</AlertTitle>
          <AlertDescription>Amazon authorization was cancelled.</AlertDescription>
        </Alert>
      ) : null}
      {returnNotice === "error" ? (
        <Alert variant="destructive">
          <AlertTitle>Amazon authorization</AlertTitle>
          <AlertDescription>Amazon authorization could not be completed.</AlertDescription>
        </Alert>
      ) : null}

      {/* 1. Top summary card: status, seller identity, authorized date, last verified, last synchronized. */}
      <Panel className="p-6">
        <div className="min-w-0 flex-1">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Connection status
          </p>
          <div className="mt-2 flex items-center gap-2.5">
            <span aria-hidden className={`inline-block h-2.5 w-2.5 rounded-full ${persistedDotClass(connectionStatus)}`} />
            <h2 className="text-lg font-semibold tracking-tight">{persistedStatusLabel(connectionStatus)}</h2>
          </div>
          {loading ? (
            <p className="mt-2 text-sm text-muted-foreground">Loading connection metadata…</p>
          ) : null}
          {connectionStatus === "connected" ? (
            <p className="mt-1 text-xs text-muted-foreground">
              Connected reflects the last successful validation, not a live connectivity guarantee.
            </p>
          ) : null}
          {waitingForAmazon ? (
            <p className="mt-2 text-sm font-medium text-foreground">Waiting for Amazon authorization</p>
          ) : null}
          {waitingForValidation && !secretLost ? (
            <p className="mt-2 text-sm font-medium text-foreground">Waiting for grant validation</p>
          ) : null}
          {secretLost ? (
            <p className="mt-2 text-sm font-medium text-foreground">Stored grant could not be read</p>
          ) : null}
          {persistedHint ? (
            <p className="mt-2 max-w-xl text-sm leading-relaxed text-muted-foreground">{persistedHint}</p>
          ) : null}
          {connecting ? (
            <p className="mt-2 text-sm text-muted-foreground">Connecting to Amazon...</p>
          ) : null}

          {attention ? (
            <Alert variant={attention.destructive ? "destructive" : "default"} className="mt-4">
              <AlertTitle>{attention.label}</AlertTitle>
              <AlertDescription>{attention.description}</AlertDescription>
            </Alert>
          ) : null}

          <dl className="mt-6 divide-y divide-border border-t border-border">
            {sellerAccountLabel ? <MetaRow label="Seller account" value={sellerAccountLabel} /> : null}
            {overview?.selling_partner_id ? (
              <MetaRow label="Seller partner ID" value={overview.selling_partner_id} />
            ) : null}
            {overview?.authorized_at ? (
              <MetaRow label="Authorized at" value={formatTimestamp(overview.authorized_at)} />
            ) : null}
            {overview?.last_successful_validation_at ? (
              <MetaRow label="Last verified" value={formatTimestamp(overview.last_successful_validation_at)} />
            ) : null}
            {overview?.last_successful_sync_at ? (
              <MetaRow label="Last synchronized" value={formatTimestamp(overview.last_successful_sync_at)} />
            ) : null}
            <MetaRow label="Provider" value={provider === "SP_API" ? "Amazon SP-API" : provider} />
            <MetaRow label="Environment" value={formatEnvironment(environment)} />
            <MetaRow label="Region" value={formatRegion(region)} />
            <MetaRow label="Persisted status" value={persistedStatusCode(connectionStatus)} />
            <MetaRow
              label="Record"
              value={persisted ? "Saved connection" : "Environment fallback"}
            />
            <MetaRow
              label="Marketplace"
              value={
                marketplace === "amazon.in"
                  ? "Amazon.in"
                  : marketplace === "amazon.com"
                    ? "Amazon.com"
                    : marketplace
              }
            />
            <MetaRow label="Application" value={application} />
            {overview?.last_error_code ? (
              <MetaRow label="Last error" value={overview.last_error_code} />
            ) : null}
          </dl>
          {overview?.authorized_at ? (
            <p className="mt-3 text-xs text-muted-foreground">
              Authorized at is the original Amazon consent date. It is not proof of current
              connectivity — see Last verified above for the latest live check.
            </p>
          ) : null}
        </div>
      </Panel>

      {/* 2. Connected Marketplaces */}
      <Section
        title="Connected Marketplaces"
        description="Canonical marketplace participation from the latest successful synchronization."
      >
        {sellerMarketplaces.length > 0 ? (
          <div className="space-y-4">
            {sync.marketplacesSectionNote ? (
              <p className="text-sm leading-relaxed text-muted-foreground">{sync.marketplacesSectionNote}</p>
            ) : null}
            <div className="grid gap-4 md:grid-cols-2">
              {sellerMarketplaces.map((item) => (
                <MarketplaceCard key={item.marketplace_id} marketplace={item} />
              ))}
            </div>
          </div>
        ) : (
          <EmptyState title="No marketplaces synchronized yet" description={sync.emptyStateDescription} />
        )}
      </Section>

      {/* 3. Connection Actions */}
      <Panel className="p-6">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0 flex-1">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Connection actions
            </p>
            <h2 className="mt-2 text-lg font-semibold tracking-tight">
              {waitingForValidation ? "Seller grant validation" : "Test connection"}
            </h2>
            {sandboxHint ? (
              <p className="mt-2 max-w-xl text-sm leading-relaxed text-muted-foreground">{sandboxHint}</p>
            ) : null}
            {showConnect && !connecting ? (
              <p className="mt-2 max-w-xl text-sm leading-relaxed text-muted-foreground">
                Connect your Amazon seller account. You will leave ASI to authorize in Seller Central.
              </p>
            ) : null}
            <div className="mt-4 flex items-center gap-2.5">
              <span
                aria-hidden
                className={`inline-block h-2.5 w-2.5 rounded-full ${sandboxDotClass(sandboxStatus)}`}
              />
              <h3 className="text-base font-semibold tracking-tight">{sandboxStatusLabel(sandboxStatus)}</h3>
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              {lastTest ? `Checked ${formatTimestamp(lastTest.tested_at)}` : "Not tested yet in this session"}
            </p>
          </div>

          <div className="flex flex-shrink-0 flex-col items-stretch gap-2 sm:flex-row">
            {showValidate ? (
              <Button onClick={() => void onTestConnection()} disabled={testing || loading || connecting}>
                {testing ? <Loader2 className="animate-spin" /> : null}
                {testing ? "Validating..." : "Validate connection"}
              </Button>
            ) : null}
            {!showValidate && !secretLost ? (
              <Button onClick={() => void onTestConnection()} disabled={testing || loading || connecting}>
                {testing ? <Loader2 className="animate-spin" /> : null}
                Test Connection
              </Button>
            ) : null}
            {showConnect ? (
              <Button onClick={() => void onConnectAmazon()} disabled={connecting || loading || testing}>
                {connecting ? <Loader2 className="animate-spin" /> : null}
                {connecting
                  ? "Connecting to Amazon..."
                  : waitingForAmazon
                    ? "Continue Amazon authorization"
                    : secretLost
                      ? "Connect Amazon again"
                      : "Connect Amazon"}
              </Button>
            ) : null}
          </div>
        </div>
      </Panel>

      {/* 4. Latest synchronization state */}
      <Panel className="p-6">
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Latest synchronization state
        </p>
        {latestIngestion ? (
          <>
            <div className="mt-2 flex items-center gap-2.5">
              <span
                aria-hidden
                className={`inline-block h-2.5 w-2.5 rounded-full ${ingestionDotClass(latestIngestion.status)}`}
              />
              <h2 className="text-lg font-semibold tracking-tight">
                {ingestionStatusLabel(latestIngestion.status)}
              </h2>
            </div>
            {latestIngestion.status === "failed" || latestIngestion.status === "timed_out" ? (
              <p className="mt-2 max-w-xl text-sm leading-relaxed text-muted-foreground">
                {failureClassLabel(latestIngestion.failure_class)}
              </p>
            ) : null}
            <dl className="mt-6 divide-y divide-border border-t border-border">
              <MetaRow label="Started at" value={formatTimestamp(latestIngestion.started_at)} />
              <MetaRow
                label="Completed at"
                value={formatTimestamp(latestIngestion.completed_at, "Not yet completed")}
              />
              <MetaRow label="Marketplaces recorded" value={String(latestIngestion.records_accepted)} />
            </dl>
          </>
        ) : (
          <p className="mt-2 max-w-xl text-sm leading-relaxed text-muted-foreground">
            No marketplace synchronization has run yet.
          </p>
        )}
      </Panel>

      <Section
        title="Future connections"
        description="These providers stay separate. This page does not ingest listings, orders, inventory, financial, or advertising data."
      >
        <div className="grid gap-4 md:grid-cols-2">
          <Panel className="p-5">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Amazon SP-API
            </p>
            <p className="mt-2 text-base font-semibold">Seller-owned data</p>
            <p className="mt-1 text-sm text-muted-foreground">Status: {sync.futureConnectionsStatus}</p>
          </Panel>
          <Panel className="p-5">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Amazon Ads API
            </p>
            <p className="mt-2 text-base font-semibold">Advertising collection</p>
            <p className="mt-1 text-sm text-muted-foreground">Status: Not connected</p>
          </Panel>
        </div>
      </Section>
    </div>
  );
}
