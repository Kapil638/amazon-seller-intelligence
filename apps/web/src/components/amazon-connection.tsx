"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2 } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { PageHeader, Panel, Section } from "@/components/ui/layout";
import { AmazonConnectionError, authorizeAmazonConnection, fetchAmazonConnection, testAmazonConnection } from "@/lib/api";
import type {
  AmazonConnectionLifecycleStatus,
  AmazonConnectionOverview,
  AmazonConnectionStatus,
  AmazonConnectionTestResult,
} from "@/lib/types";

const AUTHORIZE_START_FAILED = "Unable to start Amazon connection. Please try again.";
const AUTHORIZE_APP_NOT_CONFIGURED = "Amazon application is not configured on the API.";

type AmazonReturnNotice = "success" | "denied" | "error";

function canStartAuthorization(status: AmazonConnectionLifecycleStatus): boolean {
  return (
    status === "not_connected" ||
    status === "pending_authorization" ||
    status === "revoked" ||
    status === "error"
  );
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

function formatTimestamp(value: string | null, empty = "Not recorded"): string {
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

function MetaRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[11rem_minmax(0,1fr)] items-baseline gap-3 py-2.5">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="text-sm font-medium text-foreground">{value}</dd>
    </div>
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
  const environment = overview?.environment ?? "SANDBOX";
  const region = overview?.region ?? "eu";
  const marketplace = overview?.marketplace ?? "amazon.in";
  const application = overview?.application ?? "EWise";
  const sandboxStatus = lastTest?.status ?? null;

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
    if (connectionStatus === "pending_authorization") {
      return "Waiting for Amazon authorization. Finish consent in Seller Central. ASI will not show Connected until Amazon returns and the grant is validated.";
    }
    return "This is ASI’s stored connection record. A sandbox test does not change seller authorization.";
  }, [loading, overview, persisted, connectionStatus]);

  const sandboxHint = useMemo(() => {
    if (lastTest?.message) {
      return lastTest.message;
    }
    if (lastTest?.status === "CONNECTED") {
      return "Sandbox validation succeeded. This is not seller authorization.";
    }
    if (overview && !overview.credentials_configured) {
      return "Sandbox credentials are not configured on the API. Connection testing stays local to this page.";
    }
    return "Sandbox validation checks local SP-API credentials. It does not mark the seller as connected.";
  }, [lastTest, overview]);

  async function onTestConnection() {
    setTesting(true);
    setError(null);
    try {
      const result = await testAmazonConnection();
      setLastTest(result);
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
      const started = await authorizeAmazonConnection(environment);
      window.location.assign(started.authorization_url);
    } catch (caught) {
      setError(authorizeStartErrorMessage(caught));
      setConnecting(false);
    }
  }

  const showConnect = !loading && canStartAuthorization(connectionStatus);
  const waitingForAmazon = connectionStatus === "pending_authorization";

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
            Amazon authorization completed. ASI will not show Connected until the grant is validated.
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

      <Panel className="p-6">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0 flex-1">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Amazon connection
            </p>
            <div className="mt-2 flex items-center gap-2.5">
              <span aria-hidden className={`inline-block h-2.5 w-2.5 rounded-full ${persistedDotClass(connectionStatus)}`} />
              <h2 className="text-lg font-semibold tracking-tight">{persistedStatusLabel(connectionStatus)}</h2>
            </div>
            {loading ? (
              <p className="mt-2 text-sm text-muted-foreground">Loading connection metadata…</p>
            ) : null}
            {waitingForAmazon ? (
              <p className="mt-2 text-sm font-medium text-foreground">Waiting for Amazon authorization</p>
            ) : null}
            {persistedHint ? (
              <p className="mt-2 max-w-xl text-sm leading-relaxed text-muted-foreground">{persistedHint}</p>
            ) : null}
            {showConnect && !connecting ? (
              <p className="mt-2 max-w-xl text-sm leading-relaxed text-muted-foreground">
                Connect your Amazon seller account. You will leave ASI to authorize in Seller Central.
              </p>
            ) : null}
            {connecting ? (
              <p className="mt-2 text-sm text-muted-foreground">Connecting to Amazon...</p>
            ) : null}

            <dl className="mt-6 divide-y divide-border border-t border-border">
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
                value={marketplace === "amazon.in" ? "Amazon.in" : marketplace}
              />
              <MetaRow label="Application" value={application} />
              {overview?.selling_partner_id ? (
                <MetaRow label="Seller partner ID" value={overview.selling_partner_id} />
              ) : null}
              {overview?.authorized_at ? (
                <MetaRow label="Authorized at" value={formatTimestamp(overview.authorized_at)} />
              ) : null}
              {overview?.last_successful_validation_at ? (
                <MetaRow
                  label="Last successful validation"
                  value={formatTimestamp(overview.last_successful_validation_at)}
                />
              ) : null}
              {overview?.last_successful_sync_at ? (
                <MetaRow
                  label="Last successful sync"
                  value={formatTimestamp(overview.last_successful_sync_at)}
                />
              ) : null}
              {overview?.last_error_code ? (
                <MetaRow label="Last error" value={overview.last_error_code} />
              ) : null}
            </dl>
          </div>

          {showConnect ? (
            <Button onClick={() => void onConnectAmazon()} disabled={connecting || loading}>
              {connecting ? <Loader2 className="animate-spin" /> : null}
              {connecting
                ? "Connecting to Amazon..."
                : waitingForAmazon
                  ? "Continue Amazon authorization"
                  : "Connect Amazon"}
            </Button>
          ) : null}
        </div>
      </Panel>

      <Panel className="p-6">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0 flex-1">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Latest sandbox validation
            </p>
            <div className="mt-2 flex items-center gap-2.5">
              <span
                aria-hidden
                className={`inline-block h-2.5 w-2.5 rounded-full ${sandboxDotClass(sandboxStatus)}`}
              />
              <h2 className="text-lg font-semibold tracking-tight">{sandboxStatusLabel(sandboxStatus)}</h2>
            </div>
            {sandboxHint ? (
              <p className="mt-2 max-w-xl text-sm leading-relaxed text-muted-foreground">{sandboxHint}</p>
            ) : null}
            <dl className="mt-6 divide-y divide-border border-t border-border">
              <MetaRow
                label={
                  lastTest?.status === "FAILED"
                    ? "Failed at"
                    : lastTest?.status === "CONNECTED"
                      ? "Validated successfully at"
                      : "Last sandbox test"
                }
                value={formatTimestamp(lastTest?.tested_at ?? null, "Not tested")}
              />
            </dl>
          </div>

          <Button onClick={() => void onTestConnection()} disabled={testing || loading || connecting}>
            {testing ? <Loader2 className="animate-spin" /> : null}
            Test Connection
          </Button>
        </div>
      </Panel>

      <Section
        title="Future connections"
        description="These providers stay separate. This page does not ingest seller data, run syncs, or open analytics."
      >
        <div className="grid gap-4 md:grid-cols-2">
          <Panel className="p-5">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Amazon SP-API
            </p>
            <p className="mt-2 text-base font-semibold">Seller-owned data</p>
            <p className="mt-1 text-sm text-muted-foreground">Status: Sandbox</p>
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
