"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2 } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { PageHeader, Panel, Section } from "@/components/ui/layout";
import { AmazonConnectionError, fetchAmazonConnection, testAmazonConnection } from "@/lib/api";
import type {
  AmazonConnectionOverview,
  AmazonConnectionStatus,
  AmazonConnectionTestResult,
} from "@/lib/types";

function statusLabel(status: AmazonConnectionStatus): string {
  if (status === "CONNECTED") {
    return "Connected";
  }
  if (status === "FAILED") {
    return "Connection failed";
  }
  return "Not connected";
}

function statusDotClass(status: AmazonConnectionStatus): string {
  if (status === "CONNECTED") {
    return "bg-emerald-500";
  }
  if (status === "FAILED") {
    return "bg-destructive";
  }
  return "bg-muted-foreground/50";
}

function formatTimestamp(value: string | null): string {
  if (!value) {
    return "Not tested";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
}

function MetaRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[9.5rem_minmax(0,1fr)] items-baseline gap-3 py-2.5">
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
  const [error, setError] = useState<string | null>(null);

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

  const displayedStatus = lastTest?.status ?? overview?.status ?? "NOT_CONNECTED";
  const lastTestAt = lastTest?.tested_at ?? overview?.last_test_at ?? null;
  const provider = overview?.provider ?? lastTest?.provider ?? "SP_API";
  const environment = overview?.environment ?? lastTest?.environment ?? "SANDBOX";
  const marketplace = overview?.marketplace ?? lastTest?.marketplace ?? "amazon.in";
  const application = overview?.application ?? "EWise";

  const statusHint = useMemo(() => {
    if (lastTest?.message) {
      return lastTest.message;
    }
    if (overview && !overview.credentials_configured) {
      return "Sandbox credentials are not configured on the API. Connection testing stays local to this page.";
    }
    return null;
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

      <Panel className="p-6">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0 flex-1">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Connection status
            </p>
            <div className="mt-2 flex items-center gap-2.5">
              <span
                aria-hidden
                className={`inline-block h-2.5 w-2.5 rounded-full ${statusDotClass(displayedStatus)}`}
              />
              <p className="text-lg font-semibold tracking-tight">{statusLabel(displayedStatus)}</p>
            </div>
            {loading ? (
              <p className="mt-2 text-sm text-muted-foreground">Loading connection metadata…</p>
            ) : null}
            {statusHint ? (
              <p className="mt-2 max-w-xl text-sm leading-relaxed text-muted-foreground">{statusHint}</p>
            ) : null}

            <dl className="mt-6 divide-y divide-border border-t border-border">
              <MetaRow label="Provider" value={provider === "SP_API" ? "Amazon SP-API" : provider} />
              <MetaRow label="Environment" value={environment === "SANDBOX" ? "Sandbox" : environment} />
              <MetaRow
                label="Marketplace"
                value={marketplace === "amazon.in" ? "Amazon.in" : marketplace}
              />
              <MetaRow label="Application" value={application} />
              <MetaRow label="Last connection test" value={formatTimestamp(lastTestAt)} />
            </dl>
          </div>

          <Button onClick={() => void onTestConnection()} disabled={testing || loading}>
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
