import { render, screen } from "@testing-library/react";
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

import { CopilotActivityTimeline } from "@/components/copilot-activity-timeline";
import { CopilotConfirmationModal } from "@/components/copilot-confirmation-modal";
import { CopilotEvidenceCard } from "@/components/copilot-evidence-card";
import { CopilotMessageList } from "@/components/copilot-message-list";
import {
  ANALYZE_ASIN_PROMPT,
  SUGGESTED_PROMPTS,
  activityFromPlan,
  confirmPayload,
  containsHiddenTerm,
  evidenceCardsFromEnvelopes,
  executePayload,
  sellerErrorMessage,
} from "@/lib/copilot-view";
import type { CopilotEvidenceEnvelope, CopilotPlan, CopilotSynthesizedResponse } from "@/lib/types";

const plan: CopilotPlan = {
  plan_id: "plan-1",
  conversation_id: "conv-1",
  organization_id: "org-1",
  intent: "explain_listing_score",
  tool_calls: [{ name: "list_saved_reports", arguments: { asin: "B0TEST0001" } }],
  needs_confirmation: false,
  confirm_summary: null,
  plan_hash: "abc123",
  validation_status: "accepted",
  source: "fallback_rules",
};

const envelope: CopilotEvidenceEnvelope = {
  evidence_id: "ev-1",
  tool_name: "get_saved_report",
  organization_id: "org-1",
  produced_at: "2026-08-21T10:00:00.000Z",
  claims: [
    { key: "listing_quality_score", value: 72, kind: "historical", source: "snapshot" },
    { key: "report_id", value: "report-99", kind: "historical", source: "snapshot" },
    {
      key: "findings",
      value: [{ code: "BULLET_COUNT", message: "Add more complete bullet points." }],
      kind: "historical",
      source: "snapshot",
    },
  ],
};

describe("copilot view mappers", () => {
  it("maps plan activity without exposing internals", () => {
    const items = activityFromPlan(plan);
    expect(items.map((item) => item.label)).toContain("Checked saved analyses");
    expect(items.every((item) => !containsHiddenTerm(item.label))).toBe(true);
  });

  it("never puts confirmed on execute payloads", () => {
    expect(executePayload(plan)).toEqual({ plan_id: "plan-1", plan_hash: "abc123" });
    expect("confirmed" in executePayload(plan)).toBe(false);
  });

  it("keeps confirm payload to nonce only", () => {
    expect(confirmPayload("secret-nonce")).toEqual({ nonce: "secret-nonce" });
    expect(Object.keys(confirmPayload("secret-nonce"))).toEqual(["nonce"]);
  });

  it("builds evidence cards with history deep links", () => {
    const cards = evidenceCardsFromEnvelopes([envelope]);
    expect(cards[0]).toMatchObject({
      title: "Listing quality score",
      value: "72",
      source: "Saved listing analysis",
      href: "/history/report-99",
    });
  });

  it("uses seller-friendly errors instead of HTTP codes", () => {
    expect(sellerErrorMessage(new Error("HTTP 500"))).toBe(
      "Copilot could not complete this analysis. Please try again.",
    );
  });

  it("asks for an ASIN instead of inserting a test ASIN", () => {
    const analyze = SUGGESTED_PROMPTS.find((item) => item.id === "analyze");
    expect(analyze?.label).toBe("Analyze an ASIN");
    expect(analyze?.message).toBe("Analyze an ASIN");
    expect(SUGGESTED_PROMPTS.every((item) => !item.message.includes("B0TEST0001"))).toBe(true);
    expect(ANALYZE_ASIN_PROMPT).toContain("Please provide the ASIN you want to analyze.");
    expect(ANALYZE_ASIN_PROMPT).toContain("B01MD1SKLL");
  });
});

describe("copilot components", () => {
  it("renders empty activity copy", () => {
    render(<CopilotActivityTimeline items={[]} />);
    expect(screen.getByText("Ask a question to see what Copilot does.")).toBeInTheDocument();
  });

  it("renders activity labels", () => {
    render(
      <CopilotActivityTimeline
        items={[{ id: "1", label: "Checked saved analyses", status: "done" }]}
      />,
    );
    expect(screen.getByText("Checked saved analyses")).toBeInTheDocument();
  });

  it("renders an evidence card and report link", () => {
    render(
      <CopilotEvidenceCard
        card={{
          id: "score",
          title: "Listing quality score",
          value: "72",
          source: "Saved listing analysis",
          date: "21 Aug 2026",
          href: "/history/report-99",
          hrefLabel: "Open saved report",
        }}
      />,
    );
    expect(screen.getByText("Listing quality score")).toBeInTheDocument();
    expect(screen.getByText("72")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open saved report" })).toHaveAttribute(
      "href",
      "/history/report-99",
    );
  });

  it("renders user and assistant messages from structured evidence", () => {
    const response: CopilotSynthesizedResponse = {
      summary: "Your listing score is 72.",
      findings: ["Listing quality score: 72"],
      recommendations: ["Add more complete bullet points."],
      citations: [
        {
          evidence_id: "ev-1",
          claim_key: "listing_quality_score",
          tool_name: "get_saved_report",
          label: "Saved analysis",
        },
      ],
      confidence: "high",
      unknowns: [],
      source: "template_fallback",
      prompt_version: null,
      synthesis_model: null,
      message: "",
    };
    render(
      <CopilotMessageList
        messages={[
          { id: "u1", role: "user", content: "Why is my listing score low?" },
          { id: "a1", role: "assistant", content: response.summary, response },
        ]}
      />,
    );
    expect(screen.getByText("Why is my listing score low?")).toBeInTheDocument();
    expect(screen.getByText("Your listing score is 72.")).toBeInTheDocument();
    expect(screen.getByText("This answer was prepared from your saved evidence.")).toBeInTheDocument();
    expect(screen.queryByText(/ToolRegistry|nonce|openai/i)).not.toBeInTheDocument();
  });

  it("shows loading copy", () => {
    render(<CopilotMessageList messages={[]} loading />);
    expect(screen.getByText("Copilot is working through trusted tools…")).toBeInTheDocument();
  });

  it("renders confirmation copy without leaking a nonce", () => {
    const nonce = "nonce-secret-do-not-show";
    render(
      <CopilotConfirmationModal
        summary="Looking this up uses product credits."
        onConfirm={() => undefined}
        onCancel={() => undefined}
      />,
    );
    expect(screen.getByText("Fresh Amazon lookup required")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Continue" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
    expect(screen.queryByText(nonce)).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain(nonce);
  });
});
