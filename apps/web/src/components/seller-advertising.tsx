"use client";

import { useCallback, useEffect, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Loader2, RefreshCw } from "lucide-react";

import {
  AdsApiError,
  fetchAdsCampaigns,
  fetchAdsConnectionStatus,
  fetchAdsOverview,
  fetchAdsPerformanceSeries,
  fetchAdsProfiles,
  fetchAdsSyncStatus,
  selectAdsProfile,
} from "@/lib/api";
import { formatDateTime } from "@/lib/seller-listings-view";
import {
  ADS_PERIOD_OPTIONS,
  ADS_SYNC_STATUS_LABEL,
  defaultAdsDateRange,
  formatAdsCount,
  formatAdsMoney,
  formatAdsPercent,
  formatAdsRatio,
  parseAdsPeriod,
} from "@/lib/seller-advertising-view";
import type {
  AdsCampaign,
  AdsConnectionOverview,
  AdsOverview,
  AdsPerformancePoint,
  AdsProfile,
  AdsSyncStatusResponse,
} from "@/lib/types";
import { cn } from "@/lib/utils";

const CAMPAIGN_PAGE_SIZE = 25;

export function SellerAdvertising() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const profileParam = searchParams.get("profile");
  const period = parseAdsPeriod(searchParams.get("period"));

  const [connection, setConnection] = useState<AdsConnectionOverview | null>(null);
  const [connectionLoading, setConnectionLoading] = useState(true);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [profiles, setProfiles] = useState<AdsProfile[]>([]);
  const [selecting, setSelecting] = useState(false);

  const [syncStatus, setSyncStatus] = useState<AdsSyncStatusResponse | null>(null);
  const [overview, setOverview] = useState<AdsOverview | null>(null);
  const [overviewError, setOverviewError] = useState<string | null>(null);
  const [overviewLoading, setOverviewLoading] = useState(false);
  const [trend, setTrend] = useState<AdsPerformancePoint[]>([]);

  const [campaigns, setCampaigns] = useState<AdsCampaign[]>([]);
  const [campaignsTotal, setCampaignsTotal] = useState(0);
  const [campaignsOffset, setCampaignsOffset] = useState(0);
  const [campaignsLoading, setCampaignsLoading] = useState(false);
  const [campaignsError, setCampaignsError] = useState<string | null>(null);

  const [lastUpdatedAt, setLastUpdatedAt] = useState<string | null>(null);

  const loadConnection = useCallback(async () => {
    setConnectionLoading(true);
    setConnectionError(null);
    try {
      const [status, profileList] = await Promise.all([fetchAdsConnectionStatus(), fetchAdsProfiles()]);
      setConnection(status);
      setProfiles(profileList);
    } catch (error) {
      setConnectionError(error instanceof AdsApiError ? error.message : "Advertising data is unavailable right now.");
    } finally {
      setConnectionLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadConnection();
  }, [loadConnection]);

  const selectedProfileId =
    profileParam || connection?.selected_profile_id || profiles.find((p) => p.is_selected)?.id || null;

  const loadData = useCallback(
    async (profileId: string) => {
      const { start, end } = defaultAdsDateRange(Number(period));
      setOverviewLoading(true);
      setOverviewError(null);
      try {
        const [statusResult, overviewResult, trendResult] = await Promise.all([
          fetchAdsSyncStatus(profileId),
          fetchAdsOverview(profileId, start, end),
          fetchAdsPerformanceSeries(profileId, start, end),
        ]);
        setSyncStatus(statusResult);
        setOverview(overviewResult);
        setTrend(trendResult.points);
        setLastUpdatedAt(new Date().toISOString());
      } catch (error) {
        setOverviewError(error instanceof AdsApiError ? error.message : "Advertising metrics are unavailable right now.");
      } finally {
        setOverviewLoading(false);
      }
    },
    [period],
  );

  const loadCampaigns = useCallback(async (profileId: string, offset: number) => {
    setCampaignsLoading(true);
    setCampaignsError(null);
    try {
      const page = await fetchAdsCampaigns(profileId, { offset, limit: CAMPAIGN_PAGE_SIZE });
      setCampaigns(page.items);
      setCampaignsTotal(page.page.total);
      setCampaignsOffset(offset);
    } catch (error) {
      setCampaignsError(error instanceof AdsApiError ? error.message : "Campaigns are unavailable right now.");
    } finally {
      setCampaignsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!selectedProfileId) return;
    void loadData(selectedProfileId);
    void loadCampaigns(selectedProfileId, 0);
  }, [selectedProfileId, loadData, loadCampaigns]);

  const handleSelectProfile = useCallback(
    async (profileId: string) => {
      setSelecting(true);
      try {
        await selectAdsProfile(profileId);
        const params = new URLSearchParams(searchParams.toString());
        params.set("profile", profileId);
        router.replace(`${pathname}?${params.toString()}`);
        await loadConnection();
      } catch (error) {
        setConnectionError(error instanceof AdsApiError ? error.message : "Could not select this advertiser profile.");
      } finally {
        setSelecting(false);
      }
    },
    [router, pathname, searchParams, loadConnection],
  );

  const handlePeriodChange = (value: string) => {
    const params = new URLSearchParams(searchParams.toString());
    params.set("period", value);
    router.replace(`${pathname}?${params.toString()}`);
  };

  if (connectionLoading) {
    return <AdsLoadingSkeleton />;
  }

  if (connectionError) {
    return (
      <AdsMessagePanel
        title="Advertising is unavailable"
        message={connectionError}
        action={{ label: "Retry", onClick: () => void loadConnection() }}
      />
    );
  }

  if (!connection?.configured) {
    return (
      <AdsMessagePanel
        title="Amazon Ads is not connected yet"
        message="Advertising data becomes available here once EWise Intelligence is authorized against your Amazon Ads account and your data has synchronized."
      />
    );
  }

  if (connection.status !== "connected") {
    return (
      <AdsMessagePanel
        title="Connect your Amazon Ads account"
        message="Authorize EWise Intelligence to read your Amazon Ads advertising performance. This is read-only — no campaign, bid, or budget changes are ever made."
      />
    );
  }

  if (profiles.length === 0) {
    return <AdsMessagePanel title="No advertiser profiles found" message="No Amazon Ads advertiser profiles were returned for this connection." />;
  }

  if (!selectedProfileId) {
    return <AdsProfileSelector profiles={profiles} onSelect={handleSelectProfile} selecting={selecting} />;
  }

  return (
    <div className="flex flex-col gap-6">
      <ProfileBar profiles={profiles} selectedProfileId={selectedProfileId} onSelect={handleSelectProfile} selecting={selecting} />

      <p className="rounded-md border border-border bg-surface px-3 py-2 text-xs text-muted-foreground">
        Advertising data is available only after your Amazon Ads account is authorized and has completed
        synchronization. Figures shown reflect the most recent successful sync, not live Amazon data.
      </p>

      {syncStatus && syncStatus.status !== "synced" && (
        <div
          className={cn(
            "rounded-md border px-3 py-2 text-sm",
            syncStatus.status === "failed"
              ? "border-destructive/40 bg-destructive/10 text-destructive"
              : "border-amber-500/40 bg-amber-500/10 text-amber-800",
          )}
        >
          {ADS_SYNC_STATUS_LABEL[syncStatus.status]}
        </div>
      )}

      <div className="flex items-center justify-between">
        <div className="flex gap-2">
          {ADS_PERIOD_OPTIONS.map((option) => (
            <button
              key={option.value}
              type="button"
              onClick={() => handlePeriodChange(option.value)}
              className={cn(
                "rounded-md border px-3 py-1.5 text-sm",
                period === option.value ? "border-primary bg-primary/10 font-medium" : "border-border text-muted-foreground",
              )}
            >
              {option.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          {lastUpdatedAt && <span>Last updated {formatDateTime(lastUpdatedAt)}</span>}
          <button
            type="button"
            onClick={() => void loadData(selectedProfileId)}
            className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 hover:text-foreground"
          >
            <RefreshCw className="h-3.5 w-3.5" /> Refresh
          </button>
        </div>
      </div>

      {overviewLoading && !overview ? (
        <AdsMetricSkeleton />
      ) : overviewError ? (
        <AdsMessagePanel
          title="Metrics unavailable"
          message={overviewError}
          action={{ label: "Retry", onClick: () => void loadData(selectedProfileId) }}
        />
      ) : overview ? (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <MetricCard label="Spend" value={formatAdsMoney(overview.spend, overview.currency_code)} />
            <MetricCard label="Attributed sales" value={formatAdsMoney(overview.attributed_sales, overview.currency_code)} />
            <MetricCard label="ACOS" value={formatAdsPercent(overview.acos)} />
            <MetricCard label="ROAS" value={formatAdsRatio(overview.roas)} />
            <MetricCard label="Impressions" value={formatAdsCount(overview.impressions)} />
            <MetricCard label="Clicks" value={formatAdsCount(overview.clicks)} />
            <MetricCard label="CTR" value={formatAdsPercent(overview.ctr)} />
            <MetricCard label="CPC" value={formatAdsMoney(overview.cpc, overview.currency_code)} />
            <MetricCard label="Attributed orders" value={formatAdsCount(overview.attributed_orders)} />
          </div>
          <SpendTrendBars points={trend} />
        </>
      ) : null}

      <section>
        <h2 className="mb-2 text-sm font-medium text-muted-foreground">Campaigns</h2>
        {campaignsLoading && campaigns.length === 0 ? (
          <AdsTableSkeleton />
        ) : campaignsError ? (
          <AdsMessagePanel
            title="Campaigns unavailable"
            message={campaignsError}
            action={{ label: "Retry", onClick: () => void loadCampaigns(selectedProfileId, campaignsOffset) }}
          />
        ) : campaigns.length === 0 ? (
          <p className="rounded-md border border-border p-4 text-sm text-muted-foreground">
            No campaigns have synchronized yet.
          </p>
        ) : (
          <>
            <div className="overflow-x-auto rounded-md border border-border">
              <table className="w-full text-sm">
                <thead className="bg-surface text-left text-xs text-muted-foreground">
                  <tr>
                    <th className="px-3 py-2">Name</th>
                    <th className="px-3 py-2">State</th>
                    <th className="px-3 py-2">Daily budget</th>
                  </tr>
                </thead>
                <tbody>
                  {campaigns.map((campaign) => (
                    <tr key={campaign.id} className="border-t border-border">
                      <td className="px-3 py-2">{campaign.name}</td>
                      <td className="px-3 py-2">{campaign.state}</td>
                      <td className="px-3 py-2">{formatAdsMoney(campaign.daily_budget, campaign.currency_code)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="mt-2 flex items-center justify-between text-xs text-muted-foreground">
              <span>
                {campaignsOffset + 1}–{Math.min(campaignsOffset + CAMPAIGN_PAGE_SIZE, campaignsTotal)} of {campaignsTotal}
              </span>
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={campaignsOffset === 0}
                  onClick={() => void loadCampaigns(selectedProfileId, Math.max(0, campaignsOffset - CAMPAIGN_PAGE_SIZE))}
                  className="rounded-md border border-border px-2 py-1 disabled:opacity-40"
                >
                  Previous
                </button>
                <button
                  type="button"
                  disabled={campaignsOffset + CAMPAIGN_PAGE_SIZE >= campaignsTotal}
                  onClick={() => void loadCampaigns(selectedProfileId, campaignsOffset + CAMPAIGN_PAGE_SIZE)}
                  className="rounded-md border border-border px-2 py-1 disabled:opacity-40"
                >
                  Next
                </button>
              </div>
            </div>
          </>
        )}
      </section>
    </div>
  );
}

function ProfileBar({
  profiles,
  selectedProfileId,
  onSelect,
  selecting,
}: {
  profiles: AdsProfile[];
  selectedProfileId: string;
  onSelect: (id: string) => void;
  selecting: boolean;
}) {
  return (
    <div className="flex items-center gap-2">
      <label htmlFor="ads-profile-select" className="text-xs text-muted-foreground">
        Advertiser profile
      </label>
      <select
        id="ads-profile-select"
        value={selectedProfileId}
        disabled={selecting}
        onChange={(event) => onSelect(event.target.value)}
        className="rounded-md border border-border bg-background px-2 py-1 text-sm"
      >
        {profiles.map((profile) => (
          <option key={profile.id} value={profile.id}>
            {profile.display_name || profile.profile_id} ({profile.marketplace_country_code})
          </option>
        ))}
      </select>
      {selecting && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
    </div>
  );
}

function AdsProfileSelector({
  profiles,
  onSelect,
  selecting,
}: {
  profiles: AdsProfile[];
  onSelect: (id: string) => void;
  selecting: boolean;
}) {
  return (
    <div className="rounded-md border border-border p-6">
      <h2 className="mb-2 text-lg font-medium">Select the advertiser profile to synchronize</h2>
      <p className="mb-4 text-sm text-muted-foreground">
        Your authorization returned {profiles.length} advertiser profile{profiles.length === 1 ? "" : "s"}. Choose
        the one to use for this seller before advertising data can synchronize.
      </p>
      <div className="flex flex-col gap-2">
        {profiles.map((profile) => (
          <button
            key={profile.id}
            type="button"
            disabled={selecting}
            onClick={() => onSelect(profile.id)}
            className="flex items-center justify-between rounded-md border border-border px-3 py-2 text-left text-sm hover:border-primary disabled:opacity-50"
          >
            <span>{profile.display_name || profile.profile_id}</span>
            <span className="text-xs text-muted-foreground">
              {profile.marketplace_country_code} · {profile.currency_code}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-surface p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-lg font-semibold">{value}</p>
    </div>
  );
}

function SpendTrendBars({ points }: { points: AdsPerformancePoint[] }) {
  if (points.length === 0) return null;
  const max = Math.max(...points.map((p) => Number(p.spend) || 0), 1);
  return (
    <div className="rounded-md border border-border p-3">
      <p className="mb-2 text-xs text-muted-foreground">Daily spend trend</p>
      <div className="flex h-24 items-end gap-1">
        {points.map((point) => {
          const value = Number(point.spend) || 0;
          const heightPct = Math.max(2, (value / max) * 100);
          return (
            <div
              key={point.date}
              title={`${point.date}: ${formatAdsMoney(point.spend, null)}`}
              className="flex-1 rounded-t bg-primary/60"
              style={{ height: `${heightPct}%` }}
            />
          );
        })}
      </div>
    </div>
  );
}

function AdsMessagePanel({
  title,
  message,
  action,
}: {
  title: string;
  message: string;
  action?: { label: string; onClick: () => void };
}) {
  return (
    <div className="rounded-md border border-border p-6 text-center">
      <h2 className="mb-1 text-base font-medium">{title}</h2>
      <p className="mb-3 text-sm text-muted-foreground">{message}</p>
      {action && (
        <button type="button" onClick={action.onClick} className="rounded-md border border-border px-3 py-1.5 text-sm">
          {action.label}
        </button>
      )}
    </div>
  );
}

function AdsLoadingSkeleton() {
  return (
    <div className="flex flex-col gap-3" aria-label="Loading advertising data">
      <div className="h-8 w-48 animate-pulse rounded bg-surface" />
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="h-16 animate-pulse rounded-md bg-surface" />
        ))}
      </div>
    </div>
  );
}

function AdsMetricSkeleton() {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4" aria-label="Loading metrics">
      {Array.from({ length: 8 }).map((_, i) => (
        <div key={i} className="h-16 animate-pulse rounded-md bg-surface" />
      ))}
    </div>
  );
}

function AdsTableSkeleton() {
  return (
    <div className="flex flex-col gap-1" aria-label="Loading campaigns">
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="h-9 animate-pulse rounded bg-surface" />
      ))}
    </div>
  );
}
