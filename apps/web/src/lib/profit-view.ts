import type { ProfitEvidenceClaim, ProfitSnapshot } from "@/lib/types";

export function formatInr(value: string | null | undefined): string {
  if (value == null || value === "") {
    return "Unknown";
  }
  const amount = Number(value);
  if (Number.isNaN(amount)) {
    return "Unknown";
  }
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 2,
  }).format(amount);
}

export function formatPercent(value: string | null | undefined): string {
  if (value == null || value === "") {
    return "Unknown";
  }
  const amount = Number(value);
  if (Number.isNaN(amount)) {
    return "Unknown";
  }
  return `${(amount * 100).toFixed(1)}%`;
}

export function formatClaimValue(claim: ProfitEvidenceClaim): string {
  if (claim.kind === "unknown" || claim.value == null || claim.value === "") {
    return "Unknown";
  }
  if (typeof claim.value === "string" || typeof claim.value === "number") {
    return String(claim.value);
  }
  return JSON.stringify(claim.value);
}

export function unknownMessage(snapshot: ProfitSnapshot | null): string | null {
  if (!snapshot) {
    return null;
  }
  const messages = snapshot.completeness.messages;
  if (messages.length) {
    return messages[0];
  }
  if (snapshot.completeness.unknown.includes("cogs")) {
    return "The product profitability cannot be calculated because COGS is missing.";
  }
  return null;
}

export function savePayloadFromForm(form: {
  selling_price: string;
  cogs: string;
  referral_fee: string;
  fba_fee: string;
  shipping_cost: string;
  packaging_cost: string;
  other_cost: string;
}) {
  return {
    selling_price: emptyToNull(form.selling_price),
    cogs: emptyToNull(form.cogs),
    referral_fee_amount: emptyToNull(form.referral_fee),
    fba_fee_amount: emptyToNull(form.fba_fee),
    shipping_cost: emptyToNull(form.shipping_cost),
    packaging_cost: emptyToNull(form.packaging_cost),
    other_cost: emptyToNull(form.other_cost),
  };
}

export function formatRoas(value: string | null | undefined): string {
  if (value == null || value === "") {
    return "Unknown";
  }
  const amount = Number(value);
  if (Number.isNaN(amount)) {
    return "Unknown";
  }
  return `${amount.toFixed(2)}x`;
}

export function advertisingUnknownMessages(
  completenessMessages: string[] | undefined,
  impactMessages: string[] | undefined,
): string[] {
  const seen = new Set<string>();
  const ordered: string[] = [];
  for (const item of [...(completenessMessages ?? []), ...(impactMessages ?? [])]) {
    if (!item || seen.has(item)) {
      continue;
    }
    seen.add(item);
    ordered.push(item);
  }
  return ordered;
}

export function saveAdvertisingPayloadFromForm(form: {
  period_start: string;
  period_end: string;
  ad_spend: string;
  ad_sales: string;
  total_sales: string;
  units_in_period: string;
}) {
  return {
    period_start: emptyToNull(form.period_start),
    period_end: emptyToNull(form.period_end),
    ad_spend: emptyToNull(form.ad_spend),
    ad_sales: emptyToNull(form.ad_sales),
    total_sales: emptyToNull(form.total_sales),
    units_in_period: emptyToNull(form.units_in_period),
  };
}

function emptyToNull(value: string): string | null {
  const trimmed = value.trim();
  return trimmed === "" ? null : trimmed;
}
