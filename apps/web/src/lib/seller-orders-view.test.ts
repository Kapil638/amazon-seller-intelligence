import { describe, expect, it } from "vitest";

import {
  ORDERS_SYNC_STATUS_LABEL,
  formatFulfillmentStatus,
  formatOrdersImportedCount,
  ordersMoreHistoryRemains,
  ordersSyncShowsActiveSpinner,
} from "@/lib/seller-orders-view";
import type { OrdersSyncStatus } from "@/lib/types";

describe("ORDERS_SYNC_STATUS_LABEL", () => {
  it("never leaks a raw snake_case internal job-lifecycle token verbatim", () => {
    // Plain English words that happen to coincide with an internal status
    // name (e.g. "Queued") are fine and required by the task brief —
    // what must never appear is the raw multi-word snake_case token
    // itself (e.g. "waiting_to_retry", "timed_out").
    const rawSnakeCaseTerms = ["waiting_to_retry", "timed_out"];
    for (const label of Object.values(ORDERS_SYNC_STATUS_LABEL)) {
      for (const term of rawSnakeCaseTerms) {
        expect(label.toLowerCase()).not.toContain(term);
      }
      expect(label).not.toContain("_");
    }
  });

  it("covers every OrdersSyncStatus value", () => {
    const statuses: OrdersSyncStatus[] = [
      "never_synchronized",
      "queued",
      "running",
      "waiting_to_retry",
      "succeeded",
      "failed",
      "partial",
      "timed_out",
    ];
    for (const status of statuses) {
      expect(ORDERS_SYNC_STATUS_LABEL[status]).toBeTruthy();
    }
  });

  it("uses the required customer-friendly phrases", () => {
    expect(ORDERS_SYNC_STATUS_LABEL.queued).toBe("Queued");
    expect(ORDERS_SYNC_STATUS_LABEL.running).toBe("Importing orders");
    expect(ORDERS_SYNC_STATUS_LABEL.waiting_to_retry).toBe("Waiting for Amazon");
    expect(ORDERS_SYNC_STATUS_LABEL.succeeded).toBe("Completed");
    expect(ORDERS_SYNC_STATUS_LABEL.failed).toBe("Needs attention");
  });
});

describe("formatOrdersImportedCount", () => {
  it("formats a plural count with thousands separators", () => {
    expect(formatOrdersImportedCount(1240)).toBe("1,240 orders imported");
  });

  it("formats a singular count without an 's'", () => {
    expect(formatOrdersImportedCount(1)).toBe("1 order imported");
  });

  it("treats null/undefined as zero", () => {
    expect(formatOrdersImportedCount(null)).toBe("0 orders imported");
    expect(formatOrdersImportedCount(undefined)).toBe("0 orders imported");
  });
});

describe("ordersMoreHistoryRemains", () => {
  it("is true only while nonterminal and pagination is explicitly incomplete", () => {
    expect(ordersMoreHistoryRemains("running", false)).toBe(true);
    expect(ordersMoreHistoryRemains("waiting_to_retry", false)).toBe(true);
  });

  it("is false once pagination is complete", () => {
    expect(ordersMoreHistoryRemains("running", true)).toBe(false);
  });

  it("is false for a terminal status regardless of pagination_complete", () => {
    expect(ordersMoreHistoryRemains("succeeded", false)).toBe(false);
    expect(ordersMoreHistoryRemains("failed", false)).toBe(false);
  });

  it("is false when pagination_complete is unknown (null)", () => {
    expect(ordersMoreHistoryRemains("running", null)).toBe(false);
  });
});

describe("ordersSyncShowsActiveSpinner", () => {
  it("shows a spinner only for queued/running", () => {
    expect(ordersSyncShowsActiveSpinner("queued")).toBe(true);
    expect(ordersSyncShowsActiveSpinner("running")).toBe(true);
    expect(ordersSyncShowsActiveSpinner("waiting_to_retry")).toBe(false);
    expect(ordersSyncShowsActiveSpinner("succeeded")).toBe(false);
  });
});

describe("formatFulfillmentStatus", () => {
  it("formats a known status", () => {
    expect(formatFulfillmentStatus("PARTIALLY_SHIPPED")).toBe("Partially shipped");
  });

  it("renders an em dash for a null status", () => {
    expect(formatFulfillmentStatus(null)).toBe("—");
  });
});
