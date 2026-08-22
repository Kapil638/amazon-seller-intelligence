"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { EmptyState, Kpi, PageHeader, Panel, Section } from "@/components/ui/layout";
import {
  calculateAdvertising,
  calculateProfitModel,
  createProfitModel,
  fetchAdvertising,
  fetchProfitModel,
  listAdvertisingSnapshots,
  listProfitModels,
  ProfitError,
  updateAdvertising,
  updateProfitModel,
} from "@/lib/api";
import { isValidAsin, normalizeAsin } from "@/lib/asin";
import {
  advertisingUnknownMessages,
  formatClaimValue,
  formatInr,
  formatPercent,
  formatRoas,
  saveAdvertisingPayloadFromForm,
  savePayloadFromForm,
  unknownMessage,
} from "@/lib/profit-view";
import type {
  AdvertisingModel,
  AdvertisingSnapshotSummary,
  ProfitEvidenceClaim,
  ProfitModel,
  ProfitModelSummary,
  ProfitSnapshot,
} from "@/lib/types";

type FormState = {
  selling_price: string;
  cogs: string;
  referral_fee: string;
  fba_fee: string;
  shipping_cost: string;
  packaging_cost: string;
  other_cost: string;
};

const EMPTY_FORM: FormState = {
  selling_price: "",
  cogs: "",
  referral_fee: "",
  fba_fee: "",
  shipping_cost: "",
  packaging_cost: "",
  other_cost: "",
};

function formFromModel(model: ProfitModel): FormState {
  return {
    selling_price: model.selling_price ?? "",
    cogs: model.cogs ?? "",
    referral_fee: model.referral_fee_amount ?? "",
    fba_fee: model.fba_fee_amount ?? "",
    shipping_cost: model.shipping_cost ?? "",
    packaging_cost: model.packaging_cost ?? "",
    other_cost: model.other_cost ?? "",
  };
}

export function SellerProfit({ modelId }: { modelId?: string }) {
  if (modelId) {
    return <ProfitModelWorkspace modelId={modelId} />;
  }
  return <ProfitHome />;
}

function ProfitHome() {
  const router = useRouter();
  const [asin, setAsin] = useState("");
  const [items, setItems] = useState<ProfitModelSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const listed = await listProfitModels();
      setItems(listed.items);
    } catch (err) {
      setError(err instanceof ProfitError ? err.message : "Could not load profit models.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function onCreate() {
    const normalized = normalizeAsin(asin);
    if (!isValidAsin(normalized)) {
      setError("Enter a valid 10-character ASIN.");
      return;
    }
    setCreating(true);
    setError(null);
    try {
      const existing = await listProfitModels(normalized);
      if (existing.items.length) {
        router.push(`/profit/${existing.items[0].id}`);
        return;
      }
      const created = await createProfitModel({ asin: normalized, marketplace: "amazon.in" });
      router.push(`/profit/${created.id}`);
    } catch (err) {
      setError(err instanceof ProfitError ? err.message : "Could not create a profit model.");
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="space-y-8">
      <PageHeader
        title="Profit Intelligence"
        description="Enter product economics to see unit profit, margin, and ROI. Python calculates the numbers. Missing costs stay unknown."
      />
      {error ? (
        <Alert variant="destructive">
          <AlertTitle>Could not continue</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
      <Panel className="p-6">
        <Section title="Create a model" description="One worksheet per ASIN on amazon.in. INR only in this version.">
          <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-end">
            <div className="min-w-0 flex-1 space-y-2">
              <Label htmlFor="profit-asin">ASIN</Label>
              <Input
                id="profit-asin"
                value={asin}
                onChange={(event) => setAsin(event.target.value)}
                placeholder="B01MD1SKLL"
                autoCapitalize="characters"
              />
            </div>
            <Button onClick={() => void onCreate()} disabled={creating}>
              {creating ? <Loader2 className="animate-spin" /> : null}
              Create model
            </Button>
          </div>
        </Section>
      </Panel>
      <Section title="Saved models">
        {loading ? (
          <p className="text-sm text-muted-foreground">Loading saved models…</p>
        ) : items.length === 0 ? (
          <EmptyState
            title="No profit models yet"
            description="Create a model for an ASIN, then enter COGS and fees. Profit is not estimated."
          />
        ) : (
          <div className="grid gap-3">
            {items.map((item) => (
              <Link
                key={item.id}
                href={`/profit/${item.id}`}
                className="flex items-center justify-between rounded-lg border border-border bg-surface px-4 py-3 hover:bg-surface-subtle"
              >
                <div>
                  <p className="font-medium tabular-nums">{item.asin}</p>
                  <p className="text-xs text-muted-foreground">{item.marketplace}</p>
                </div>
                <StatusBadge status={item.latest_status} unknown={item.unknown} />
              </Link>
            ))}
          </div>
        )}
      </Section>
    </div>
  );
}

function ProfitModelWorkspace({ modelId }: { modelId: string }) {
  const [model, setModel] = useState<ProfitModel | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const next = await fetchProfitModel(modelId);
      setModel(next);
      setForm(formFromModel(next));
    } catch (err) {
      setError(err instanceof ProfitError ? err.message : "Could not load this profit model.");
    } finally {
      setLoading(false);
    }
  }, [modelId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function persistAndCalculate() {
    setSaving(true);
    setError(null);
    try {
      await updateProfitModel(modelId, savePayloadFromForm(form));
      const calculated = await calculateProfitModel(modelId);
      setModel(calculated);
      setForm(formFromModel(calculated));
    } catch (err) {
      setError(err instanceof ProfitError ? err.message : "Could not calculate profit.");
    } finally {
      setSaving(false);
    }
  }

  const snapshot = model?.latest_snapshot ?? null;
  const missing = unknownMessage(snapshot);

  if (loading) {
    return <p className="text-sm text-muted-foreground">Loading profit model…</p>;
  }
  if (!model) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Model not found</AlertTitle>
        <AlertDescription>{error || "This profit model was not found."}</AlertDescription>
      </Alert>
    );
  }

  return (
    <div className="space-y-8">
      <PageHeader
        title={`Profit · ${model.asin}`}
        description={`${model.marketplace} · ${model.currency}. Calculations use profit-calc-v1. The browser does not compute profit.`}
      >
        <Button asChild variant="outline">
          <Link href="/profit">All models</Link>
        </Button>
      </PageHeader>
      {error ? (
        <Alert variant="destructive">
          <AlertTitle>Could not continue</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
      {missing ? (
        <Alert>
          <AlertTitle>Unknown values</AlertTitle>
          <AlertDescription>{missing}</AlertDescription>
        </Alert>
      ) : null}
      <Section
        title="Unit Economics"
        description="Product cost and fees before advertising. Python calculates profit with profit-calc-v1."
      >
        <div className="mt-4 grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <InputsPanel form={form} onChange={setForm} onCalculate={() => void persistAndCalculate()} saving={saving} />
          <OutputsPanel snapshot={snapshot} />
        </div>
      </Section>
      <AdvertisingIntelligencePanel modelId={modelId} profitSnapshotId={snapshot?.id ?? null} />
      <EvidencePanel snapshot={snapshot} />
    </div>
  );
}

type AdsFormState = {
  period_start: string;
  period_end: string;
  ad_spend: string;
  ad_sales: string;
  total_sales: string;
  units_in_period: string;
};

const EMPTY_ADS_FORM: AdsFormState = {
  period_start: "",
  period_end: "",
  ad_spend: "",
  ad_sales: "",
  total_sales: "",
  units_in_period: "",
};

function adsFormFromModel(model: AdvertisingModel): AdsFormState {
  return {
    period_start: model.period_start ?? "",
    period_end: model.period_end ?? "",
    ad_spend: model.ad_spend ?? "",
    ad_sales: model.ad_sales ?? "",
    total_sales: model.total_sales ?? "",
    units_in_period: model.units_in_period ?? "",
  };
}

function AdvertisingIntelligencePanel({
  modelId,
  profitSnapshotId,
}: {
  modelId: string;
  profitSnapshotId: string | null;
}) {
  const [model, setModel] = useState<AdvertisingModel | null>(null);
  const [history, setHistory] = useState<AdvertisingSnapshotSummary[]>([]);
  const [form, setForm] = useState<AdsFormState>(EMPTY_ADS_FORM);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [next, listed] = await Promise.all([
        fetchAdvertising(modelId),
        listAdvertisingSnapshots(modelId),
      ]);
      setModel(next);
      setForm(adsFormFromModel(next));
      setHistory(listed.items);
    } catch (err) {
      setError(err instanceof ProfitError ? err.message : "Could not load advertising data.");
    } finally {
      setLoading(false);
    }
  }, [modelId, profitSnapshotId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function persistAndCalculate() {
    setSaving(true);
    setError(null);
    try {
      await updateAdvertising(modelId, saveAdvertisingPayloadFromForm(form));
      const calculated = await calculateAdvertising(modelId);
      setModel(calculated);
      setForm(adsFormFromModel(calculated));
      const listed = await listAdvertisingSnapshots(modelId);
      setHistory(listed.items);
    } catch (err) {
      setError(err instanceof ProfitError ? err.message : "Could not calculate advertising impact.");
    } finally {
      setSaving(false);
    }
  }

  const snapshot = model?.latest_snapshot ?? null;
  const outputs = snapshot?.outputs;
  const impact = snapshot?.impact ?? model?.impact ?? null;
  const inputs = snapshot?.inputs;
  const unknownCopy = advertisingUnknownMessages(
    snapshot?.completeness.messages,
    impact?.messages,
  );

  return (
    <Section
      title="Advertising Intelligence"
      description="Period advertising inputs. Python calculates ACOS, TACOS, ROAS, and after-ads profit. The browser does not compute those metrics."
    >
      {error ? (
        <Alert variant="destructive" className="mt-4">
          <AlertTitle>Could not continue</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
      {unknownCopy.length ? (
        <Alert className="mt-4">
          <AlertTitle>Unknown advertising values</AlertTitle>
          <AlertDescription>
            {unknownCopy.map((item) => (
              <p key={item}>{item}</p>
            ))}
          </AlertDescription>
        </Alert>
      ) : null}
      {model?.profit_snapshot_stale ? (
        <Alert className="mt-4">
          <AlertTitle>Unit economics changed</AlertTitle>
          <AlertDescription>
            Unit economics changed since this ads snapshot. Recalculate advertising
            impact. After-ads profit is not a matched monthly P&L — it uses the cited
            unit snapshot plus this advertising period.
          </AlertDescription>
        </Alert>
      ) : null}
      {loading ? (
        <p className="mt-4 text-sm text-muted-foreground">Loading advertising inputs…</p>
      ) : (
        <div className="mt-4 grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <Panel className="p-6">
            <Section
              title="Advertising inputs"
              description="Seller-entered period totals. Blank means unknown — it is not treated as zero."
            >
              <div className="mt-4 grid gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label htmlFor="ads-period-start">Period start</Label>
                  <Input
                    id="ads-period-start"
                    type="date"
                    value={form.period_start}
                    onChange={(event) => setForm({ ...form, period_start: event.target.value })}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="ads-period-end">Period end</Label>
                  <Input
                    id="ads-period-end"
                    type="date"
                    value={form.period_end}
                    onChange={(event) => setForm({ ...form, period_end: event.target.value })}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="ads-spend">Ad spend</Label>
                  <Input
                    id="ads-spend"
                    inputMode="decimal"
                    value={form.ad_spend}
                    onChange={(event) => setForm({ ...form, ad_spend: event.target.value })}
                    placeholder="0.00"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="ads-sales">Ad sales</Label>
                  <Input
                    id="ads-sales"
                    inputMode="decimal"
                    value={form.ad_sales}
                    onChange={(event) => setForm({ ...form, ad_sales: event.target.value })}
                    placeholder="0.00"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="ads-total-sales">Total sales</Label>
                  <Input
                    id="ads-total-sales"
                    inputMode="decimal"
                    value={form.total_sales}
                    onChange={(event) => setForm({ ...form, total_sales: event.target.value })}
                    placeholder="Required for TACOS"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="ads-units">Units in period</Label>
                  <Input
                    id="ads-units"
                    inputMode="decimal"
                    value={form.units_in_period}
                    onChange={(event) => setForm({ ...form, units_in_period: event.target.value })}
                    placeholder="Required for after-ads profit"
                  />
                </div>
              </div>
              <div className="mt-6">
                <Button onClick={() => void persistAndCalculate()} disabled={saving}>
                  {saving ? <Loader2 className="animate-spin" /> : null}
                  Calculate advertising impact
                </Button>
              </div>
            </Section>
          </Panel>
          <Panel className="p-6">
            <Section
              title="Advertising results"
              description="Values come from the API. ACOS, TACOS, and ROAS are not calculated in the browser."
            >
              <div className="mt-4 grid gap-6 sm:grid-cols-2">
                <Kpi label="ACOS" value={formatPercent(outputs?.acos)} />
                <Kpi label="TACOS" value={formatPercent(outputs?.tacos)} />
                <Kpi label="ROAS" value={formatRoas(outputs?.roas)} />
                <Kpi label="Ad spend" value={formatInr(inputs?.ad_spend)} />
                <Kpi label="Ad sales" value={formatInr(inputs?.ad_sales)} />
                <Kpi label="Net profit after ads" value={formatInr(impact?.net_profit_after_ads)} hint="Per unit" />
                <Kpi
                  label="Break-even ACOS"
                  value={formatPercent(impact?.break_even_acos)}
                  hint="Pre-ads margin. Not a TACOS cap. Volume and other costs assumed constant."
                />
              </div>
              <div className="mt-4">
                <StatusBadge status={snapshot?.status ?? null} unknown={snapshot?.completeness.unknown ?? []} />
              </div>
            </Section>
          </Panel>
        </div>
      )}
      <div className="mt-6">
        <Section title="Advertising history" description="Previous snapshots are read-only. Saving inputs and calculating creates a new snapshot.">
          {history.length === 0 ? (
            <EmptyState
              title="No advertising snapshots yet"
              description="Enter a period and calculate to create an immutable advertising snapshot."
            />
          ) : (
            <div className="mt-3 overflow-x-auto rounded-lg border border-border">
              <table className="w-full text-sm">
                <thead className="bg-surface-subtle text-left text-muted-foreground">
                  <tr>
                    <th className="px-4 py-2 font-medium">Period</th>
                    <th className="px-4 py-2 font-medium">ACOS</th>
                    <th className="px-4 py-2 font-medium">TACOS</th>
                    <th className="px-4 py-2 font-medium">Status</th>
                    <th className="px-4 py-2 font-medium">Calculated</th>
                  </tr>
                </thead>
                <tbody>
                  {history.map((row) => (
                    <tr key={row.id} className="border-t border-border">
                      <td className="px-4 py-2 tabular-nums">
                        {row.period_start && row.period_end
                          ? `${row.period_start} – ${row.period_end}`
                          : "Unknown period"}
                      </td>
                      <td className="px-4 py-2 tabular-nums">{formatPercent(row.acos)}</td>
                      <td className="px-4 py-2 tabular-nums">{formatPercent(row.tacos)}</td>
                      <td className="px-4 py-2">
                        <StatusBadge status={row.status} unknown={[]} />
                      </td>
                      <td className="px-4 py-2 text-muted-foreground">
                        {new Date(row.calculated_at).toLocaleString()}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Section>
      </div>
    </Section>
  );
}

function InputsPanel({
  form,
  onChange,
  onCalculate,
  saving,
}: {
  form: FormState;
  onChange: (next: FormState) => void;
  onCalculate: () => void;
  saving: boolean;
}) {
  function field(key: keyof FormState, label: string, hint?: string) {
    return (
      <div className="space-y-2">
        <Label htmlFor={`profit-${key}`}>{label}</Label>
        <Input
          id={`profit-${key}`}
          inputMode="decimal"
          value={form[key]}
          onChange={(event) => onChange({ ...form, [key]: event.target.value })}
          placeholder="0.00"
        />
        {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
      </div>
    );
  }

  return (
    <Panel className="p-6">
      <Section
        title="Inputs"
        description="Seller-entered economics. Enter 0 if a cost does not apply. Blank means unknown — it is not treated as zero."
      >
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          {field("selling_price", "Selling price")}
          {field("cogs", "COGS / product cost")}
          {field("referral_fee", "Referral fee", "Seller assumption, not a live Amazon quote.")}
          {field("fba_fee", "FBA fee", "Seller assumption, not a live Amazon quote.")}
          {field("shipping_cost", "Shipping")}
          {field("packaging_cost", "Packaging")}
          {field("other_cost", "Other costs")}
        </div>
        <div className="mt-6">
          <Button onClick={onCalculate} disabled={saving}>
            {saving ? <Loader2 className="animate-spin" /> : null}
            Save and calculate
          </Button>
        </div>
      </Section>
    </Panel>
  );
}

function OutputsPanel({ snapshot }: { snapshot: ProfitSnapshot | null }) {
  const outputs = snapshot?.outputs;
  return (
    <Panel className="p-6">
      <Section title="Results" description="Values come from the API. Margin and ROI are not calculated in the browser.">
        <div className="mt-4 grid gap-6 sm:grid-cols-3">
          <Kpi label="Net profit" value={formatInr(outputs?.net_profit_before_ads)} hint="Before ads" />
          <Kpi label="Margin" value={formatPercent(outputs?.margin_before_ads)} hint="Before ads" />
          <Kpi label="ROI on COGS" value={formatPercent(outputs?.roi_on_cogs)} />
        </div>
        <dl className="mt-6 grid gap-3 text-sm">
          <Line label="Amazon fees" value={formatInr(outputs?.amazon_fees)} />
          <Line label="Operating costs" value={formatInr(outputs?.operating_costs)} />
          <Line label="Landed cost" value={formatInr(outputs?.landed_cost)} />
        </dl>
        <div className="mt-4">
          <StatusBadge status={snapshot?.status ?? null} unknown={snapshot?.completeness.unknown ?? []} />
        </div>
      </Section>
    </Panel>
  );
}

function EvidencePanel({ snapshot }: { snapshot: ProfitSnapshot | null }) {
  const claims = snapshot?.evidence.claims ?? [];
  const visible = useMemo(
    () => claims.filter((claim) => claim.key !== "completeness"),
    [claims],
  );
  return (
    <Section
      title="Evidence"
      description="Each number is labeled as seller-provided, calculated, or unknown. Fees are seller assumptions until Amazon fee APIs exist."
    >
      {!snapshot ? (
        <EmptyState
          title="No snapshot yet"
          description="Save and calculate to create an immutable profit snapshot."
        />
      ) : (
        <div className="space-y-4">
          <p className="text-sm text-muted-foreground">
            Formula {snapshot.profit_formula_version} · calculated {new Date(snapshot.calculated_at).toLocaleString()}
          </p>
          {snapshot.completeness.unknown.length ? (
            <p className="text-sm">
              Unknown fields: {snapshot.completeness.unknown.join(", ")}
            </p>
          ) : null}
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {visible.map((claim) => (
              <EvidenceClaimCard key={claim.key} claim={claim} />
            ))}
          </div>
        </div>
      )}
    </Section>
  );
}

function EvidenceClaimCard({ claim }: { claim: ProfitEvidenceClaim }) {
  return (
    <article className="rounded-lg border border-border bg-surface p-4 shadow-[var(--shadow-sm)]">
      <p className="text-xs text-muted-foreground">{claim.source}</p>
      <h3 className="mt-1 text-sm font-medium">{claim.key.replaceAll("_", " ")}</h3>
      <p className="mt-1 text-lg font-semibold tabular-nums tracking-tight">{formatClaimValue(claim)}</p>
      <p className="mt-1 text-xs text-muted-foreground">{claim.kind}</p>
      {claim.notes ? <p className="mt-2 text-xs text-muted-foreground">{claim.notes}</p> : null}
    </article>
  );
}

function Line({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="tabular-nums font-medium">{value}</dd>
    </div>
  );
}

function StatusBadge({ status, unknown }: { status: string | null; unknown: string[] }) {
  if (!status) {
    return <Badge variant="outline">Not calculated</Badge>;
  }
  if (status === "complete") {
    return <Badge>Complete</Badge>;
  }
  if (status === "partial") {
    return <Badge variant="secondary">{unknown.includes("cogs") ? "COGS missing" : "Partial"}</Badge>;
  }
  return <Badge variant="outline">{status}</Badge>;
}
