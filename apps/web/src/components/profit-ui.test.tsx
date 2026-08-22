import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
  }: {
    href: string;
    children: React.ReactNode;
  }) => <a href={href}>{children}</a>,
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/lib/api", () => ({
  ProfitError: class ProfitError extends Error {},
  listProfitModels: vi.fn(async () => ({ items: [], total: 0 })),
  createProfitModel: vi.fn(),
  fetchProfitModel: vi.fn(),
  updateProfitModel: vi.fn(),
  calculateProfitModel: vi.fn(),
  previewProfit: vi.fn(),
  fetchAdvertising: vi.fn(async () => ({
    id: "ads-1",
    organization_id: "org-1",
    profit_model_id: "model-1",
    asin: "B0TEST0001",
    marketplace: "amazon.in",
    currency: "INR",
    source: "seller_input",
    period_start: null,
    period_end: null,
    ad_spend: null,
    ad_sales: null,
    total_sales: null,
    units_in_period: null,
    latest_snapshot: null,
    impact: null,
    profit_snapshot_stale: false,
    created_at: "2026-08-22T10:00:00.000Z",
    updated_at: "2026-08-22T10:00:00.000Z",
  })),
  updateAdvertising: vi.fn(),
  calculateAdvertising: vi.fn(),
  listAdvertisingSnapshots: vi.fn(async () => ({ items: [], total: 0 })),
  previewAdvertising: vi.fn(),
}));

import { SellerProfit } from "@/components/seller-profit";
import {
  advertisingUnknownMessages,
  formatInr,
  formatPercent,
  formatRoas,
  saveAdvertisingPayloadFromForm,
  savePayloadFromForm,
  unknownMessage,
} from "@/lib/profit-view";
import type { ProfitSnapshot } from "@/lib/types";
import { fetchProfitModel } from "@/lib/api";

const snapshot: ProfitSnapshot = {
  id: "snap-1",
  organization_id: "org-1",
  profit_model_id: "model-1",
  status: "partial",
  profit_formula_version: "profit-calc-v1",
  inputs: {
    selling_price: "999.00",
    cogs: null,
    referral_fee: "80.00",
    fba_fee: "190.00",
    shipping_cost: "0.00",
    packaging_cost: "0.00",
    other_cost: "0.00",
  },
  outputs: {
    amazon_fees: "270.00",
    operating_costs: "0.00",
    landed_cost: null,
    net_profit_before_ads: null,
    margin_before_ads: null,
    roi_on_cogs: null,
  },
  completeness: {
    unknown: ["cogs"],
    messages: ["The product profitability cannot be calculated because COGS is missing."],
  },
  evidence: {
    evidence_id: "ev-1",
    tool_name: "profit_calculation",
    organization_id: "org-1",
    produced_at: "2026-08-21T10:00:00.000Z",
    claims: [],
  },
  calculated_at: "2026-08-21T10:00:00.000Z",
};

describe("profit view mappers", () => {
  it("formats API money and percents without recalculating", () => {
    expect(formatInr("379.00")).toContain("379");
    expect(formatPercent("0.379379")).toBe("37.9%");
    expect(formatInr(null)).toBe("Unknown");
    expect(formatPercent(null)).toBe("Unknown");
  });

  it("never puts calculated profit fields on save payloads", () => {
    const payload = savePayloadFromForm({
      selling_price: "999",
      cogs: "350",
      referral_fee: "80",
      fba_fee: "190",
      shipping_cost: "0",
      packaging_cost: "0",
      other_cost: "0",
    });
    expect(payload).toEqual({
      selling_price: "999",
      cogs: "350",
      referral_fee_amount: "80",
      fba_fee_amount: "190",
      shipping_cost: "0",
      packaging_cost: "0",
      other_cost: "0",
    });
    expect("net_profit" in payload).toBe(false);
    expect("margin" in payload).toBe(false);
    expect("roi" in payload).toBe(false);
  });

  it("never puts calculated advertising metrics on save payloads", () => {
    const payload = saveAdvertisingPayloadFromForm({
      period_start: "2026-08-01",
      period_end: "2026-08-31",
      ad_spend: "320",
      ad_sales: "1000",
      total_sales: "2000",
      units_in_period: "10",
    });
    expect(payload).toEqual({
      period_start: "2026-08-01",
      period_end: "2026-08-31",
      ad_spend: "320",
      ad_sales: "1000",
      total_sales: "2000",
      units_in_period: "10",
    });
    expect("acos" in payload).toBe(false);
    expect("tacos" in payload).toBe(false);
    expect("roas" in payload).toBe(false);
    expect("net_profit_after_ads" in payload).toBe(false);
  });

  it("formats ROAS from the API without recalculating ACOS", () => {
    expect(formatRoas("3.125000")).toBe("3.13x");
    expect(formatRoas(null)).toBe("Unknown");
    expect(advertisingUnknownMessages(
      ["TACOS unavailable because total sales are missing."],
      ["After-ads profit unavailable because units are missing."],
    )).toEqual([
      "TACOS unavailable because total sales are missing.",
      "After-ads profit unavailable because units are missing.",
    ]);
  });

  it("surfaces the missing COGS message from the snapshot", () => {
    expect(unknownMessage(snapshot)).toBe(
      "The product profitability cannot be calculated because COGS is missing.",
    );
  });
});

describe("profit workspace", () => {
  it("renders the Profit workspace, not inside Copilot", async () => {
    render(<SellerProfit />);
    expect(screen.getByRole("heading", { name: "Profit Intelligence" })).toBeInTheDocument();
    expect(screen.getByLabelText("ASIN")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText("No profit models yet")).toBeInTheDocument();
    });
  });

  it("renders Advertising Intelligence inside the profit model, without a separate Ads nav", async () => {
    vi.mocked(fetchProfitModel).mockResolvedValueOnce({
      id: "model-1",
      organization_id: "org-1",
      asin: "B0TEST0001",
      marketplace: "amazon.in",
      currency: "INR",
      selling_price: "999.00",
      selling_price_source: "seller",
      cogs: "350.00",
      shipping_cost: "0.00",
      packaging_cost: "0.00",
      other_cost: "0.00",
      referral_fee_amount: "80.00",
      fba_fee_amount: "190.00",
      fee_category_key: null,
      latest_snapshot: snapshot,
      created_at: "2026-08-22T10:00:00.000Z",
      updated_at: "2026-08-22T10:00:00.000Z",
    });
    render(<SellerProfit modelId="model-1" />);
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Unit Economics" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Calculate advertising impact" })).toBeInTheDocument();
    });
    expect(screen.getByRole("heading", { name: "Advertising Intelligence" })).toBeInTheDocument();
    expect(screen.getByText(/Volume and other costs assumed constant/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Advertising" })).not.toBeInTheDocument();
  });
});
