import * as React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a>,
}));

vi.mock("@/lib/api", () => ({
  AmazonConnectionError: class AmazonConnectionError extends Error {
    kind: string;
    constructor(message: string, kind = "unknown") {
      super(message);
      this.kind = kind;
    }
  },
  createCopilotConversation: vi.fn(),
  fetchCopilotConversation: vi.fn(),
  planCopilotTurn: vi.fn(),
  executeCopilotPlan: vi.fn(),
  confirmCopilotPlan: vi.fn(),
  synthesizeCopilot: vi.fn(),
  fetchAmazonConnection: vi.fn(),
  fetchListingsSummary: vi.fn(),
  fetchOrdersSummary: vi.fn(),
}));

import { SellerCopilot } from "@/components/seller-copilot";
import {
  createCopilotConversation,
  executeCopilotPlan,
  fetchAmazonConnection,
  fetchCopilotConversation,
  fetchListingsSummary,
  fetchOrdersSummary,
  planCopilotTurn,
  synthesizeCopilot,
} from "@/lib/api";
import { SKILL_SUGGESTIONS, SUGGESTED_PROMPTS } from "@/lib/copilot-view";
import type {
  AmazonConnectionOverview,
  CopilotConversationDetail,
  CopilotEvidenceEnvelope,
  CopilotExecutionResult,
  CopilotPlan,
  CopilotSynthesizedResponse,
  ListingsSummary,
  OrdersSummary,
} from "@/lib/types";

const US_ID = "11111111-1111-4111-8111-111111111111";
const MX_ID = "22222222-2222-4222-8222-222222222222";

function overview(overrides: Partial<AmazonConnectionOverview> = {}): AmazonConnectionOverview {
  return {
    status: "CONNECTED",
    connection_status: "connected",
    persisted: true,
    provider: "SP_API",
    environment: "PRODUCTION",
    region: "na",
    marketplace: "amazon.com",
    application: "EWise",
    credentials_configured: true,
    selling_partner_id: "A1SELLERID",
    authorized_at: "2026-08-25T05:00:00.000Z",
    last_successful_validation_at: "2026-08-25T05:01:00.000Z",
    last_successful_sync_at: null,
    last_error_code: null,
    last_test_at: null,
    organization_id: "33333333-3333-4333-8333-333333333333",
    seller_account_id: "44444444-4444-4444-8444-444444444444",
    seller_account_display_name: "Synthetic Test Store",
    marketplaces: [
      {
        id: US_ID,
        marketplace_id: "ATVPDKIKX0DER",
        name: "Amazon.com",
        country_code: "US",
        domain_name: "www.amazon.com",
        is_participating: true,
        has_suspended_listings: false,
        is_active: true,
        last_seen_at: "2026-08-29T00:00:00.000Z",
      },
      {
        id: MX_ID,
        marketplace_id: "A1AM78C64UM0Y8",
        name: "Amazon.com.mx",
        country_code: "MX",
        domain_name: "www.amazon.com.mx",
        is_participating: true,
        has_suspended_listings: false,
        is_active: true,
        last_seen_at: "2026-08-29T00:00:00.000Z",
      },
    ],
    latest_ingestion: null,
    ads_api: { provider: "ADS_API", status: "NOT_CONNECTED" },
    ...overrides,
  };
}

function listingsSummary(overrides: Partial<ListingsSummary> = {}): ListingsSummary {
  return {
    marketplace_participation_id: US_ID,
    total_listings: 10,
    active_count: 10,
    inactive_count: 0,
    buyable_count: 9,
    not_buyable_count: 1,
    discoverable_count: 10,
    not_discoverable_count: 0,
    with_issues_count: 1,
    without_issues_count: 9,
    issue_severity_error_count: 1,
    issue_severity_warning_count: 0,
    issue_severity_info_count: 0,
    with_asin_count: 10,
    with_consumer_price_count: 10,
    with_fulfillment_availability_count: 10,
    sync: {
      status: "succeeded",
      failure_class: null,
      queued_at: null,
      started_at: null,
      completed_at: "2026-08-29T00:00:00.000Z",
      pages_fetched: 1,
      records_received: 10,
      records_accepted: 10,
      records_rejected: 0,
      reported_total_results: 10,
      pagination_complete: true,
      last_successful_synchronized_at: "2026-08-29T00:00:00.000Z",
      next_retry_at: null,
    },
    ...overrides,
  };
}

function ordersSummary(overrides: Partial<OrdersSummary> = {}): OrdersSummary {
  return {
    marketplace_participation_id: US_ID,
    total_orders: 153,
    cancelled_count: 2,
    business_order_count: 0,
    prime_order_count: 20,
    status_counts: { SHIPPED: 150, CANCELLED: 2, UNSHIPPED: 1 },
    order_value_sum: "1530.0000",
    order_value_currency: "USD",
    sync: {
      status: "succeeded",
      failure_class: null,
      queued_at: null,
      started_at: null,
      completed_at: "2026-08-29T00:00:00.000Z",
      pages_fetched: 2,
      orders_received: 153,
      orders_accepted: 153,
      orders_rejected: 0,
      items_received: 154,
      items_accepted: 154,
      items_rejected: 0,
      pagination_complete: true,
      last_successful_synchronized_at: "2026-08-29T00:00:00.000Z",
      next_retry_at: null,
    },
    ...overrides,
  };
}

const conversation: CopilotConversationDetail = {
  id: "conv-1",
  organization_id: "org-1",
  status: "active",
  title: null,
  last_asin: null,
  last_report_id: null,
  previous_intent: null,
  compact_context: {
    last_asin: null,
    last_report_id: null,
    previous_intent: null,
    pending_confirmation: null,
    evidence_refs: [],
    recent_user_snippets: [],
  },
  pending_confirmation: null,
};

const plan: CopilotPlan = {
  plan_id: "plan-1",
  conversation_id: "conv-1",
  organization_id: "org-1",
  intent: "prioritize_listing_health",
  tool_calls: [{ name: "prioritize_listing_health", arguments: { marketplace_participation_id: US_ID } }],
  needs_confirmation: false,
  confirm_summary: null,
  plan_hash: "hash-1",
  validation_status: "accepted",
  source: "fallback_rules",
};

const skillEvidenceEnvelope: CopilotEvidenceEnvelope = {
  evidence_id: "ev-1",
  tool_name: "prioritize_listing_health",
  organization_id: "org-1",
  produced_at: "2026-08-29T00:00:00.000Z",
  claims: [
    {
      key: "skill_evidence",
      kind: "calculated",
      source: "prioritize_listing_health",
      value: {
        skill_id: "listing_health_prioritizer",
        skill_version: "1.0.0",
        marketplace_participation_ids: [US_ID],
        listings_freshness: { status: "succeeded", last_successful_synchronized_at: "2026-08-29T00:00:00.000Z" },
        orders_freshness: { status: "succeeded", last_successful_synchronized_at: "2026-08-29T00:00:00.000Z" },
        has_newer_incomplete_run: false,
        metrics: { total_listings: 10 },
        records: [{ seller_sku: "SKU-ERR" }],
        limitations: ["Cannot explain why Amazon raised an issue beyond its own code and severity."],
        confidence: "high",
        deep_links: [{ label: "View listings sorted by issue severity", href: "/seller/listings?sort=severity" }],
        generated_at: "2026-08-29T00:00:00.000Z",
      },
    },
  ],
};

const execution: CopilotExecutionResult = {
  plan_id: "plan-1",
  conversation_id: "conv-1",
  organization_id: "org-1",
  plan_hash: "hash-1",
  status: "succeeded",
  confirmation_required: false,
  confirmation_nonce: null,
  confirm_summary: null,
  evidence: [skillEvidenceEnvelope],
  tool_results: [
    { name: "prioritize_listing_health", status: "succeeded", evidence: null, error_code: null, error_message: null },
  ],
};

const response: CopilotSynthesizedResponse = {
  summary: "SKU-ERR needs attention first.",
  findings: ["Data freshness — Listings data: succeeded", "SKU-ERR has 1 error-level issue"],
  recommendations: ["Start with SKU-ERR — it ranks first."],
  citations: [],
  confidence: "high",
  unknowns: [],
  source: "template_fallback",
  prompt_version: null,
  synthesis_model: null,
  message: "SKU-ERR needs attention first.",
};

beforeEach(() => {
  vi.mocked(fetchAmazonConnection).mockResolvedValue(overview());
  vi.mocked(fetchListingsSummary).mockResolvedValue(listingsSummary());
  vi.mocked(fetchOrdersSummary).mockResolvedValue(ordersSummary());
  vi.mocked(createCopilotConversation).mockResolvedValue(conversation);
  vi.mocked(fetchCopilotConversation).mockResolvedValue(conversation);
  vi.mocked(planCopilotTurn).mockResolvedValue(plan);
  vi.mocked(executeCopilotPlan).mockResolvedValue(execution);
  vi.mocked(synthesizeCopilot).mockResolvedValue(response);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function waitForReady() {
  await waitFor(() => expect(screen.getByRole("option", { name: /Amazon\.com \(US\)/ })).toBeInTheDocument());
}

describe("SellerCopilot — scope controls", () => {
  it("shows the marketplace selector when more than one marketplace is connected", async () => {
    render(<SellerCopilot />);
    await waitForReady();
    expect(screen.getByRole("option", { name: /Amazon\.com\.mx \(MX\)/ })).toBeInTheDocument();
  });

  it("hides the marketplace selector for a single-marketplace connection", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(
      overview({ marketplaces: [overview().marketplaces![0]] }),
    );
    render(<SellerCopilot />);
    await waitFor(() => expect(fetchListingsSummary).toHaveBeenCalled());
    expect(screen.queryByLabelText("Marketplace")).not.toBeInTheDocument();
  });

  it("shows the selected marketplace and period above the conversation", async () => {
    render(<SellerCopilot />);
    await waitForReady();
    const summary = screen.getByText(/Showing/);
    expect(summary).toBeInTheDocument();
    expect(summary.textContent).toContain("Amazon.com");
    expect(summary.textContent).toContain("Last 30 days");
  });

  it("lets the seller change the analysis period and threads it into the next request", async () => {
    render(<SellerCopilot />);
    await waitForReady();
    fireEvent.change(screen.getByLabelText("Analysis period"), { target: { value: "7" } });
    fireEvent.click(screen.getByText(SKILL_SUGGESTIONS[0].question));
    await waitFor(() => expect(planCopilotTurn).toHaveBeenCalled());
    expect(planCopilotTurn).toHaveBeenCalledWith(
      "conv-1",
      SKILL_SUGGESTIONS[0].question,
      { marketplaceParticipationId: US_ID, periodDays: 7 },
    );
  });

  it("shows listings and orders freshness once loaded", async () => {
    render(<SellerCopilot />);
    await waitFor(() => expect(screen.getAllByText(/succeeded/i).length).toBeGreaterThanOrEqual(2));
    expect(screen.getByText(/Listings:/)).toBeInTheDocument();
    expect(screen.getByText(/Orders:/)).toBeInTheDocument();
  });
});

describe("SellerCopilot — launch skill cards", () => {
  it("renders the new empty-state heading and copy", async () => {
    render(<SellerCopilot />);
    await waitForReady();
    expect(screen.getByText("Ask Copilot about your seller business")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Use your synchronized Listings and Orders data to identify risks, understand performance, and decide what needs attention.",
      ),
    ).toBeInTheDocument();
  });

  it("renders all five launch skill cards as their customer-facing question plus an explanatory line", async () => {
    render(<SellerCopilot />);
    await waitForReady();
    for (const skill of SKILL_SUGGESTIONS) {
      expect(screen.getByText(skill.question)).toBeInTheDocument();
      expect(screen.getByText(skill.explanation)).toBeInTheDocument();
    }
  });

  it.each(SKILL_SUGGESTIONS)(
    "submits exactly its own question with the current scope: $question",
    async (skill) => {
      render(<SellerCopilot />);
      await waitForReady();
      fireEvent.click(screen.getByText(skill.question));
      await waitFor(() => expect(planCopilotTurn).toHaveBeenCalled());
      expect(planCopilotTurn).toHaveBeenCalledWith("conv-1", skill.question, {
        marketplaceParticipationId: US_ID,
        periodDays: 30,
      });
    },
  );

  it("runs a launch skill card end to end and renders a six-section evidence-backed answer", async () => {
    render(<SellerCopilot />);
    await waitForReady();

    fireEvent.click(screen.getByText(SKILL_SUGGESTIONS[0].question));

    await waitFor(() => expect(screen.getByText("SKU-ERR needs attention first.")).toBeInTheDocument());
    const answerCard = screen.getByText("SKU-ERR needs attention first.").closest("div.space-y-4") as HTMLElement;
    expect(within(answerCard).getByText("Answer")).toBeInTheDocument();
    expect(within(answerCard).getByText("Evidence")).toBeInTheDocument();
    expect(within(answerCard).getByText("Data freshness")).toBeInTheDocument();
    expect(within(answerCard).getByText("Suggested next step")).toBeInTheDocument();
    expect(within(answerCard).getByText("Limitations")).toBeInTheDocument();
    expect(within(answerCard).getByText("View supporting data")).toBeInTheDocument();
    expect(
      within(answerCard).getByRole("link", { name: "View listings sorted by issue severity" }),
    ).toHaveAttribute("href", "/seller/listings?sort=severity");
    expect(screen.queryByText(/token_reference|nonce|prioritize_listing_health|skill_id/i)).not.toBeInTheDocument();
  });

  it("disables launch skill cards while a marketplace has not loaded or is not selected", async () => {
    vi.mocked(fetchAmazonConnection).mockResolvedValue(overview({ marketplaces: [] }));
    render(<SellerCopilot />);
    await waitFor(() => expect(fetchAmazonConnection).toHaveBeenCalled());
    const card = screen.getByText(SKILL_SUGGESTIONS[0].question).closest("button");
    expect(card).toBeDisabled();
    expect(screen.getByText(/Connect an Amazon marketplace/)).toBeInTheDocument();
  });

  it("prevents duplicate submission while a request is running", async () => {
    // The launch-skill card itself disappears the instant the first
    // question is asked (the empty state gives way to the conversation),
    // so a seller physically cannot double-click the same card — the
    // meaningful place to prove duplicate-submission prevention is the
    // free-text Ask control, which stays mounted for the whole session.
    let resolvePlan!: (value: CopilotPlan) => void;
    vi.mocked(planCopilotTurn).mockReturnValue(
      new Promise((resolve) => {
        resolvePlan = resolve;
      }),
    );
    render(<SellerCopilot />);
    await waitForReady();

    fireEvent.change(screen.getByPlaceholderText("Which listings should I fix first?"), {
      target: { value: "Which listings should I fix first?" },
    });
    const askButton = screen.getByRole("button", { name: /ask/i });
    fireEvent.click(askButton);
    expect(askButton).toBeDisabled();
    fireEvent.click(askButton);
    fireEvent.click(askButton);

    resolvePlan(plan);
    await waitFor(() => expect(planCopilotTurn).toHaveBeenCalledTimes(1));
  });

  it("re-enables the launch skill cards for a follow-up question once the answer has arrived", async () => {
    render(<SellerCopilot />);
    await waitForReady();
    fireEvent.click(screen.getByText(SKILL_SUGGESTIONS[0].question));
    await waitFor(() => expect(screen.getByText("SKU-ERR needs attention first.")).toBeInTheDocument());
    // Once answered, loading is false again — a follow-up question through
    // the always-visible Ask control must not be blocked (the button is
    // still disabled for an empty draft, which is separate and correct).
    fireEvent.change(screen.getByPlaceholderText("Which listings should I fix first?"), {
      target: { value: "How are my orders trending?" },
    });
    expect(screen.getByRole("button", { name: /ask/i })).not.toBeDisabled();
  });
});

describe("SellerCopilot — legacy product research", () => {
  it("keeps saved-analysis and ASIN research actions accessible in a secondary section", async () => {
    render(<SellerCopilot />);
    await waitForReady();
    expect(screen.getByText("Product research")).toBeInTheDocument();
    for (const item of SUGGESTED_PROMPTS) {
      expect(screen.getByRole("button", { name: item.label })).toBeInTheDocument();
    }
  });

  it("submits a legacy product-research action like any other question", async () => {
    render(<SellerCopilot />);
    await waitForReady();
    fireEvent.click(screen.getByRole("button", { name: "Analyze an ASIN" }));
    await waitFor(() => expect(planCopilotTurn).toHaveBeenCalled());
    expect(planCopilotTurn).toHaveBeenCalledWith("conv-1", "Analyze an ASIN", {
      marketplaceParticipationId: US_ID,
      periodDays: 30,
    });
  });
});

describe("SellerCopilot — responsive layout", () => {
  it("uses a single-column card layout on mobile that widens on larger screens", async () => {
    render(<SellerCopilot />);
    await waitForReady();
    const grid = screen.getByText(SKILL_SUGGESTIONS[0].question).closest("div.grid");
    expect(grid).toHaveClass("grid-cols-1");
    expect(grid?.className).toMatch(/sm:grid-cols-2/);
    expect(grid?.className).toMatch(/lg:grid-cols-3/);
  });

  it("never introduces a horizontally-scrolling fixed-width container around the scope controls", async () => {
    render(<SellerCopilot />);
    await waitForReady();
    const scopeBar = screen.getByText(/Analysis period/).closest("div.flex");
    expect(scopeBar?.className).toMatch(/flex-wrap/);
  });
});
