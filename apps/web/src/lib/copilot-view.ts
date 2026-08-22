import type {
  CopilotEvidenceEnvelope,
  CopilotPlan,
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

export const SUGGESTED_PROMPTS = [
  { id: "score", label: "Why is my listing score low?", message: "Why is my listing score low?" },
  { id: "history", label: "Previous reports", message: "Show my saved history" },
  { id: "analyze", label: "Analyze an ASIN", message: "Analyze an ASIN" },
  { id: "changed", label: "What changed recently?", message: "What changed vs last analysis?" },
] as const;

const INTENT_LABELS: Record<string, string> = {
  explain_listing_score: "Explaining your listing score",
  summarize_report: "Summarizing a saved analysis",
  list_history: "Reviewing saved analyses",
  analyze_asin: "Analyzing this ASIN",
  what_changed: "Looking at what changed",
  out_of_scope: "This question is outside Copilot",
  clarify: "Need a bit more detail",
};

const TOOL_ACTIVITY: Record<string, string> = {
  list_saved_reports: "Checked saved analyses",
  get_saved_report: "Retrieved saved listing analysis",
  analyze_listing_v2: "Ran listing analysis",
  get_product: "Looked up current product data",
};

const TOOL_SOURCE: Record<string, string> = {
  list_saved_reports: "Saved analyses",
  get_saved_report: "Saved listing analysis",
  analyze_listing_v2: "Listing analysis",
  get_product: "Product lookup",
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
  }
  return cards;
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
