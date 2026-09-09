import type {
  InventoryHealthDemandEligibility,
  InventoryHealthFreshnessState,
  InventoryHealthInventoryState,
  InventoryHealthOverlay,
} from "@/lib/types";

/**
 * 12B.6C — customer-friendly Inventory Health language. Never presents
 * an unavailable calculation as `0` — every label below corresponds to
 * an explicit backend state, never a UI-invented fallback for `null`.
 */
export const INVENTORY_HEALTH_STATE_LABEL: Record<InventoryHealthInventoryState, string> = {
  inactive: "Inactive",
  out_of_stock: "Out of fulfillable stock",
  low_coverage: "Low coverage",
  healthy_coverage: "Healthy coverage",
  high_coverage: "High coverage",
  unclassified: "Not yet classified",
};

export const INVENTORY_HEALTH_ELIGIBILITY_LABEL: Record<InventoryHealthDemandEligibility, string> = {
  eligible: "Demand evidence available",
  no_eligible_sales_traffic_fact: "No reliable demand evidence",
  insufficient_window: "Demand window too short",
  unsupported_condition: "Condition not supported for demand analysis",
  missing_identity: "Missing SKU identity",
};

export const INVENTORY_HEALTH_FRESHNESS_LABEL: Record<InventoryHealthFreshnessState, string> = {
  fresh: "Fresh",
  stale_inventory: "Inventory data is stale",
  stale_sales: "Sales data is stale",
  stale_both: "Inventory and sales data are stale",
};

export const INVENTORY_HEALTH_OVERLAY_LABEL: Record<InventoryHealthOverlay, string> = {
  demand_with_no_fulfillable_stock: "Demand with no fulfillable stock",
  inbound_present: "Inbound replenishment present",
  unfulfillable_present: "Unfulfillable inventory present",
  researching_present: "Under Amazon research",
  no_recent_demand: "No recent demand",
};

/** Every proposed state, matching `inventory_health_formulas.py`'s own
 * `InventoryState` enum exactly — used to seed the summary card grid so
 * a state with a genuine 0 count still renders (never silently omitted). */
export const INVENTORY_HEALTH_STATES: InventoryHealthInventoryState[] = [
  "out_of_stock",
  "low_coverage",
  "healthy_coverage",
  "high_coverage",
  "unclassified",
  "inactive",
];

const NUMBER_FORMAT = new Intl.NumberFormat();
const DECIMAL_FORMAT = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 });

export function formatInventoryHealthQuantity(value: number | null | undefined): string {
  if (value == null) return "—";
  return NUMBER_FORMAT.format(value);
}

/** Rounds to 1 decimal place for display only — every formula computes
 * at full float precision; only the last step, rendering, rounds. */
export function formatInventoryHealthDays(value: number | null | undefined): string {
  if (value == null) return "—";
  return `${DECIMAL_FORMAT.format(value)}d`;
}

export function formatInventoryHealthVelocity(value: number | null | undefined): string {
  if (value == null) return "—";
  return `${DECIMAL_FORMAT.format(value)}/day`;
}

export function inventoryHealthStateBadgeTone(
  state: InventoryHealthInventoryState,
): "danger" | "warning" | "success" | "neutral" {
  if (state === "out_of_stock") return "danger";
  if (state === "low_coverage") return "warning";
  if (state === "healthy_coverage") return "success";
  return "neutral";
}
