import type {
  CopilotEvidenceEnvelope,
  CopilotPlan,
  CopilotSkillEvidence,
  CopilotSynthesizedResponse,
  CopilotToolCallResult,
} from "@/lib/types";

export type ActivityStatus = "done" | "active" | "blocked" | "failed";

export type ActivityItem = {
  id: string;
  label: string;
  status: ActivityStatus;
};

export type EvidenceCardModel = {
  id: string;
  title: string;
  value: string;
  source: string;
  date?: string;
  href?: string;
  hrefLabel?: string;
};

export const ANALYZE_ASIN_PROMPT =
  "Please provide the ASIN you want to analyze.\n\nExample: B01MD1SKLL";

export const COPILOT_EMPTY_HEADING = "Ask Copilot about your seller business";
export const COPILOT_EMPTY_DESCRIPTION =
  "Use your synchronized Listings and Orders data to identify risks, understand performance, and decide what needs attention.";

export const PRODUCT_RESEARCH_HEADING = "Product research";

// Legacy saved-analysis/ASIN research shortcuts — kept, but demoted to a
// secondary "Product research" section beneath the five launch-skill
// cards below (see SKILL_SUGGESTIONS).
export const SUGGESTED_PROMPTS = [
  { id: "score", label: "Why is my listing score low?", message: "Why is my listing score low?" },
  { id: "history", label: "Review previous analyses", message: "Show my saved history" },
  { id: "analyze", label: "Analyze an ASIN", message: "Analyze an ASIN" },
  { id: "changed", label: "What changed in my saved analyses?", message: "What changed vs last analysis?" },
] as const;

// 12B.5A/12B.5A-UI — the five launch skills, shown as prominent
// suggestion cards. `question` is both the customer-facing headline and
// the exact text submitted when the card is clicked — a free-form
// question expressing the same intent routes identically, since both
// paths go through the same planner/tool validation boundary.
export const SKILL_SUGGESTIONS = [
  {
    id: "listing-health",
    question: "Which listings should I fix first?",
    explanation: "We rank your listings by issue severity, buyability, and recent order activity.",
  },
  {
    id: "non-buyable",
    question: "Why are my listings not buyable?",
    explanation: "We check status and issues, starting with a prioritized list if more than one qualifies.",
  },
  {
    id: "order-trends",
    question: "How are my orders trending?",
    explanation: "Orders, units, and order value for your selected period vs. the one before it.",
  },
  {
    id: "cancellations",
    question: "Are cancellations unusually high?",
    explanation: "We compare your current cancellation rate against your own recent history.",
  },
  {
    id: "listing-risk",
    question: "Which listing issues affect the most orders?",
    explanation: "See which open issues are tied to the most already-observed order activity.",
  },
] as const;

export type PeriodOption = { value: number; label: string };

export const PERIOD_OPTIONS: PeriodOption[] = [
  { value: 7, label: "Last 7 days" },
  { value: 30, label: "Last 30 days" },
  { value: 90, label: "Last 90 days" },
];

export const DEFAULT_PERIOD_DAYS = 30;

export function periodLabel(days: number | null | undefined): string {
  const match = PERIOD_OPTIONS.find((option) => option.value === days);
  return match ? match.label : "Last 30 days";
}

const INTENT_LABELS: Record<string, string> = {
  explain_listing_score: "Explaining your listing score",
  summarize_report: "Summarizing a saved analysis",
  list_history: "Reviewing saved analyses",
  analyze_asin: "Analyzing this ASIN",
  what_changed: "Looking at what changed",
  explain_profit: "Explaining profit evidence",
  explain_advertising_impact: "Explaining advertising impact",
  out_of_scope: "This question is outside Copilot",
  clarify: "Need a bit more detail",
  prioritize_listing_health: "Ranking your listings by urgency",
  investigate_non_buyable_listing: "Investigating this listing",
  analyze_order_trends: "Analyzing your order trend",
  detect_cancellation_anomalies: "Checking your cancellation rate",
  rank_listing_risk_by_order_exposure: "Ranking listing risk by order exposure",
};

const TOOL_ACTIVITY: Record<string, string> = {
  list_saved_reports: "Checked saved analyses",
  get_saved_report: "Retrieved saved listing analysis",
  analyze_listing_v2: "Ran listing analysis",
  get_product: "Looked up current product data",
  get_profit_snapshot: "Retrieved profit snapshot",
  analyze_profitability: "Ran profit calculation",
  get_advertising_snapshot: "Retrieved advertising snapshot",
  analyze_advertising_impact: "Composed advertising impact",
  prioritize_listing_health: "Ranked listings by issue severity and order exposure",
  investigate_non_buyable_listing: "Checked buyability, issues, and order evidence",
  analyze_order_trends: "Computed order and unit trends",
  detect_cancellation_anomalies: "Computed cancellation rate and threshold check",
  rank_listing_risk_by_order_exposure: "Joined listing issues with order exposure",
};

const TOOL_SOURCE: Record<string, string> = {
  list_saved_reports: "Saved analyses",
  get_saved_report: "Saved listing analysis",
  analyze_listing_v2: "Listing analysis",
  get_product: "Product lookup",
  get_profit_snapshot: "Profit snapshot",
  analyze_profitability: "Profit calculation",
  get_advertising_snapshot: "Advertising snapshot",
  analyze_advertising_impact: "Advertising impact",
  prioritize_listing_health: "Listing health ranking",
  investigate_non_buyable_listing: "Non-buyable investigation",
  analyze_order_trends: "Order and sales trend",
  detect_cancellation_anomalies: "Cancellation analysis",
  rank_listing_risk_by_order_exposure: "Listing risk by order exposure",
};

const HIDDEN_TERMS = [
  "nonce",
  "api_key",
  "openai",
  "toolregistry",
  "evidenceenvelope",
  "copilot_plan",
  "copilot_synthesize",
  "handler",
];

export function intentLabel(intent: string): string {
  return INTENT_LABELS[intent] || "Working on your question";
}

export function executePayload(plan: Pick<CopilotPlan, "plan_id" | "plan_hash">): {
  plan_id: string;
  plan_hash: string;
} {
  return { plan_id: plan.plan_id, plan_hash: plan.plan_hash };
}

export function confirmPayload(nonce: string): { nonce: string } {
  return { nonce };
}

export function activityFromPlan(plan: CopilotPlan): ActivityItem[] {
  const items: ActivityItem[] = [
    { id: "understood", label: intentLabel(plan.intent), status: "done" },
  ];
  for (const call of plan.tool_calls) {
    items.push({
      id: `tool-${call.name}`,
      label: TOOL_ACTIVITY[call.name] || "Used trusted listing evidence",
      status: "done",
    });
  }
  if (plan.needs_confirmation) {
    items.push({
      id: "confirm",
      label: "Waiting for confirmation to look up Amazon data",
      status: "blocked",
    });
  }
  return items;
}

export function activityFromExecution(
  plan: CopilotPlan,
  toolResults: CopilotToolCallResult[],
  options: { phase: "running" | "blocked" | "answered" | "failed" },
): ActivityItem[] {
  const phase = options.phase;
  const items: ActivityItem[] = [
    { id: "understood", label: intentLabel(plan.intent), status: "done" },
  ];
  if (!plan.tool_calls.length) {
    items.push({
      id: "answer",
      label: phase === "answered" ? "Prepared answer" : "Preparing answer",
      status: phase === "answered" ? "done" : "active",
    });
    return items;
  }
  for (const call of plan.tool_calls) {
    const result = toolResults.find((row) => row.name === call.name);
    const status = resultStatus(result, phase);
    items.push({
      id: `tool-${call.name}`,
      label: TOOL_ACTIVITY[call.name] || "Used trusted listing evidence",
      status,
    });
  }
  if (phase === "blocked") {
    items.push({
      id: "confirm",
      label: "Waiting for confirmation to look up Amazon data",
      status: "blocked",
    });
  } else if (phase === "failed") {
    items.push({ id: "answer", label: "Could not finish this analysis", status: "failed" });
  } else {
    items.push({
      id: "evidence",
      label: "Retrieved evidence",
      status: phase === "answered" ? "done" : "active",
    });
    items.push({
      id: "answer",
      label: phase === "answered" ? "Prepared answer" : "Preparing answer",
      status: phase === "answered" ? "done" : "active",
    });
  }
  return items;
}

export function evidenceCardsFromEnvelopes(envelopes: CopilotEvidenceEnvelope[]): EvidenceCardModel[] {
  const cards: EvidenceCardModel[] = [];
  for (const envelope of envelopes) {
    const claims = Object.fromEntries(envelope.claims.map((item) => [item.key, item]));
    const source = TOOL_SOURCE[envelope.tool_name] || "Analysis evidence";
    const date = formatEvidenceDate(
      (claims.analysis_timestamp?.value as string | null) || claims.listing_quality_score?.as_of || envelope.produced_at,
    );
    const reportId = stringClaim(claims.report_id?.value);
    if (claims.listing_quality_score) {
      cards.push({
        id: `${envelope.evidence_id}-score`,
        title: "Listing quality score",
        value: String(claims.listing_quality_score.value ?? "—"),
        source,
        date,
        href: reportId ? `/history/${reportId}` : undefined,
        hrefLabel: reportId ? "Open saved report" : undefined,
      });
    }
    const findings = claims.findings?.value;
    if (Array.isArray(findings) && findings.length) {
      const first = findings[0] as { message?: string; code?: string };
      cards.push({
        id: `${envelope.evidence_id}-findings`,
        title: "Key finding",
        value: first.message || first.code || "Listing finding",
        source,
        date,
        href: reportId ? `/history/${reportId}` : undefined,
        hrefLabel: reportId ? "Open saved report" : undefined,
      });
    }
    const reports = claims.reports?.value;
    if (Array.isArray(reports)) {
      for (const row of reports.slice(0, 4)) {
        if (!row || typeof row !== "object") continue;
        const item = row as { report_id?: string; asin?: string; listing_quality_score?: number };
        if (!item.report_id) continue;
        cards.push({
          id: item.report_id,
          title: `Saved analysis ${item.asin || ""}`.trim(),
          value: item.listing_quality_score != null ? `Score ${item.listing_quality_score}` : "Saved report",
          source,
          date,
          href: `/history/${item.report_id}`,
          hrefLabel: "Open saved report",
        });
      }
    }
    if (claims.title && envelope.tool_name === "get_product") {
      cards.push({
        id: `${envelope.evidence_id}-title`,
        title: "Product",
        value: String(claims.title.value ?? "—"),
        source,
        date,
      });
    }
    if (claims.net_profit_before_ads) {
      cards.push({
        id: `${envelope.evidence_id}-profit`,
        title: "Unit profit before ads",
        value: claims.net_profit_before_ads.value == null ? "Unknown" : String(claims.net_profit_before_ads.value),
        source,
        date,
        href: "/profit",
        hrefLabel: "Open Profit",
      });
    }
    const skillEvidence = claims.skill_evidence?.value as CopilotSkillEvidence | undefined;
    if (skillEvidence && typeof skillEvidence === "object") {
      cards.push(...skillEvidenceCards(envelope.evidence_id, skillEvidence, source));
    }
    if (claims.acos) {
      cards.push({
        id: `${envelope.evidence_id}-acos`,
        title: "ACOS",
        value: claims.acos.value == null ? "Unknown" : String(claims.acos.value),
        source,
        date,
        href: "/profit",
        hrefLabel: "Open Profit",
      });
    }
  }
  return cards;
}

const SKILL_TITLES: Record<string, string> = {
  listing_health_prioritizer: "Listing health ranking",
  non_buyable_listing_investigator: "Non-buyable listing investigation",
  order_and_sales_trend_analyst: "Order and sales trend",
  cancellation_operational_anomaly_detector: "Cancellation analysis",
  listing_risk_by_order_exposure: "Listing risk by order exposure",
};

function skillEvidenceCards(
  evidenceId: string,
  evidence: CopilotSkillEvidence,
  source: string,
): EvidenceCardModel[] {
  const cards: EvidenceCardModel[] = [];
  const title = SKILL_TITLES[evidence.skill_id] || "Skill evidence";
  const confidenceLabel =
    evidence.confidence === "insufficient_data" ? "Not enough data yet" : `Confidence: ${evidence.confidence}`;
  cards.push({
    id: `${evidenceId}-skill-confidence`,
    title,
    value: confidenceLabel,
    source,
  });

  const freshness = describeSkillFreshness(evidence);
  if (freshness.line) {
    cards.push({
      id: `${evidenceId}-skill-freshness`,
      title: "Data freshness",
      value: freshness.line,
      source,
    });
    if (freshness.warning) {
      cards.push({
        id: `${evidenceId}-skill-freshness-warning`,
        title: "Freshness warning",
        value: freshness.warning,
        source,
      });
    }
  }

  for (const link of evidence.deep_links || []) {
    cards.push({
      id: `${evidenceId}-skill-link-${link.href}`,
      title: "View data",
      value: link.label,
      source,
      href: link.href,
      hrefLabel: link.label,
    });
  }

  return cards;
}

export function describeSkillFreshness(evidence: CopilotSkillEvidence): {
  line: string | null;
  warning: string | null;
} {
  const listingsThrough = evidence.listings_freshness?.last_successful_synchronized_at;
  const ordersThrough = evidence.orders_freshness?.last_successful_synchronized_at;
  if (!evidence.listings_freshness && !evidence.orders_freshness) {
    return { line: null, warning: null };
  }
  const parts: string[] = [];
  if (evidence.listings_freshness) {
    parts.push(
      `Listings ${evidence.listings_freshness.status.replace(/_/g, " ")}${listingsThrough ? ` (${formatEvidenceDate(listingsThrough)})` : ""}`,
    );
  }
  if (evidence.orders_freshness) {
    parts.push(
      `Orders ${evidence.orders_freshness.status.replace(/_/g, " ")}${ordersThrough ? ` (${formatEvidenceDate(ordersThrough)})` : ""}`,
    );
  }
  return {
    line: parts.join(" · "),
    warning: evidence.has_newer_incomplete_run
      ? "A newer synchronization has not completed successfully yet — this answer may not reflect the very latest data."
      : null,
  };
}

// Finds the first skill's structured evidence in a turn's evidence
// envelopes, so the per-message answer can render Data freshness,
// Limitations, and View-data links directly from typed data instead of
// parsing them back out of the synthesized message text.
export function extractSkillEvidence(envelopes: CopilotEvidenceEnvelope[]): CopilotSkillEvidence | null {
  for (const envelope of envelopes) {
    const claim = envelope.claims.find((item) => item.key === "skill_evidence");
    if (claim && claim.value && typeof claim.value === "object") {
      return claim.value as CopilotSkillEvidence;
    }
  }
  return null;
}

export function fallbackNotice(response: CopilotSynthesizedResponse | null): string | null {
  if (!response) return null;
  if (response.source === "template_fallback") {
    return "This answer was prepared from your saved evidence.";
  }
  return null;
}

export function sellerErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message && !/http\s*\d+/i.test(error.message)) {
    if (!containsHiddenTerm(error.message)) {
      return error.message;
    }
  }
  return "Copilot could not complete this analysis. Please try again.";
}

export function containsHiddenTerm(value: string): boolean {
  const lowered = value.toLowerCase();
  return HIDDEN_TERMS.some((term) => lowered.includes(term));
}

export function visibleTextIsSafe(value: string): boolean {
  return !containsHiddenTerm(value);
}

function resultStatus(result: CopilotToolCallResult | undefined, phase: string): ActivityStatus {
  if (result?.status === "failed") return "failed";
  if (result?.status === "blocked_confirmation" || phase === "blocked") return "blocked";
  if (result?.status === "succeeded" || phase === "answered") return "done";
  if (phase === "failed") return "failed";
  return "active";
}

function stringClaim(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

function formatEvidenceDate(value: string | null | undefined): string | undefined {
  if (!value) return undefined;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return undefined;
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" }).format(date);
}
