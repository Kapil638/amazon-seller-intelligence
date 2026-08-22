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
  calculateProfitModel,
  createProfitModel,
  fetchProfitModel,
  listProfitModels,
  ProfitError,
  updateProfitModel,
} from "@/lib/api";
import { isValidAsin, normalizeAsin } from "@/lib/asin";
import {
  formatClaimValue,
  formatInr,
  formatPercent,
  savePayloadFromForm,
  unknownMessage,
} from "@/lib/profit-view";
import type {
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
        description="Product cost and fees. Python calculates profit with profit-calc-v1."
      >
        <div className="mt-4 grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <InputsPanel form={form} onChange={setForm} onCalculate={() => void persistAndCalculate()} saving={saving} />
          <OutputsPanel snapshot={snapshot} />
        </div>
      </Section>
      <EvidencePanel snapshot={snapshot} />
    </div>
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
