"use client";

import { FormEvent, useCallback, useRef, useState } from "react";
import { Loader2, Send } from "lucide-react";

import { CopilotActivityTimeline } from "@/components/copilot-activity-timeline";
import { CopilotConfirmationModal } from "@/components/copilot-confirmation-modal";
import { CopilotEvidenceList } from "@/components/copilot-evidence-card";
import { CopilotMessageList, type CopilotChatMessage } from "@/components/copilot-message-list";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { EmptyState, PageHeader, Panel } from "@/components/ui/layout";
import { Textarea } from "@/components/ui/textarea";
import {
  confirmCopilotPlan,
  createCopilotConversation,
  executeCopilotPlan,
  fetchCopilotConversation,
  planCopilotTurn,
  synthesizeCopilot,
} from "@/lib/api";
import type { CopilotExecutionResult, CopilotPlan, CopilotSynthesizedResponse } from "@/lib/types";
import {
  ANALYZE_ASIN_PROMPT,
  SUGGESTED_PROMPTS,
  activityFromExecution,
  activityFromPlan,
  executePayload,
  evidenceCardsFromEnvelopes,
  intentLabel,
  sellerErrorMessage,
  type ActivityItem,
  type EvidenceCardModel,
} from "@/lib/copilot-view";

type PendingConfirm = {
  plan: CopilotPlan;
  userMessage: string;
  summary: string;
};

export function SellerCopilot() {
  const [draft, setDraft] = useState("");
  const [messages, setMessages] = useState<CopilotChatMessage[]>([]);
  const [activity, setActivity] = useState<ActivityItem[]>([]);
  const [evidence, setEvidence] = useState<EvidenceCardModel[]>([]);
  const [understood, setUnderstood] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingConfirm, setPendingConfirm] = useState<PendingConfirm | null>(null);
  const conversationIdRef = useRef<string | null>(null);
  const nonceRef = useRef<string | null>(null);

  const appendAssistant = useCallback((response: CopilotSynthesizedResponse) => {
    setMessages((current) => [
      ...current,
      {
        id: `assistant-${Date.now()}`,
        role: "assistant",
        content: response.summary,
        response,
      },
    ]);
  }, []);

  const ensureConversation = useCallback(async () => {
    if (conversationIdRef.current) {
      return conversationIdRef.current;
    }
    const created = await createCopilotConversation();
    conversationIdRef.current = created.id;
    return created.id;
  }, []);

  const finishWithEvidence = useCallback(
    async (plan: CopilotPlan, userMessage: string, execution: CopilotExecutionResult) => {
      setActivity(activityFromExecution(plan, execution.tool_results, { phase: "running" }));
      const conversation = await fetchCopilotConversation(plan.conversation_id);
      const response = await synthesizeCopilot({
        user_message: userMessage,
        intent: plan.intent,
        evidence: execution.evidence,
        compact_context: conversation.compact_context,
      });
      appendAssistant(response);
      setEvidence(evidenceCardsFromEnvelopes(execution.evidence));
      setActivity(activityFromExecution(plan, execution.tool_results, { phase: "answered" }));
    },
    [appendAssistant],
  );

  const runTurn = useCallback(
    async (rawMessage: string) => {
      const userMessage = rawMessage.trim();
      if (!userMessage || loading) {
        return;
      }
      setDraft("");
      setError(null);
      setPendingConfirm(null);
      nonceRef.current = null;
      setLoading(true);
      setMessages((current) => [
        ...current,
        { id: `user-${Date.now()}`, role: "user", content: userMessage },
      ]);
      try {
        const conversationId = await ensureConversation();
        const plan = await planCopilotTurn(conversationId, userMessage);
        setUnderstood(intentLabel(plan.intent));
        setActivity(activityFromPlan(plan));
        if (!plan.tool_calls.length) {
          if (plan.intent === "analyze_asin") {
            setMessages((current) => [
              ...current,
              {
                id: `assistant-${Date.now()}`,
                role: "assistant",
                content: ANALYZE_ASIN_PROMPT,
              },
            ]);
            setEvidence([]);
            setActivity(activityFromExecution(plan, [], { phase: "answered" }));
            return;
          }
          const conversation = await fetchCopilotConversation(conversationId);
          const response = await synthesizeCopilot({
            user_message: userMessage,
            intent: plan.intent,
            evidence: [],
            compact_context: conversation.compact_context,
          });
          appendAssistant(response);
          setEvidence([]);
          setActivity(activityFromExecution(plan, [], { phase: "answered" }));
          return;
        }
        const execution = await executeCopilotPlan(conversationId, executePayload(plan));
        if (execution.status === "blocked_confirmation") {
          nonceRef.current = execution.confirmation_nonce;
          setPendingConfirm({
            plan,
            userMessage,
            summary: execution.confirm_summary || plan.confirm_summary || "",
          });
          setActivity(activityFromExecution(plan, execution.tool_results, { phase: "blocked" }));
          return;
        }
        if (execution.status === "failed") {
          setActivity(activityFromExecution(plan, execution.tool_results, { phase: "failed" }));
          setError("Copilot could not complete this analysis. Please try again.");
          return;
        }
        await finishWithEvidence(plan, userMessage, execution);
      } catch (err) {
        setError(sellerErrorMessage(err));
      } finally {
        setLoading(false);
      }
    },
    [appendAssistant, ensureConversation, finishWithEvidence, loading],
  );

  const onConfirm = useCallback(async () => {
    const pending = pendingConfirm;
    const nonce = nonceRef.current;
    if (!pending || !nonce || !conversationIdRef.current) {
      setPendingConfirm(null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const execution = await confirmCopilotPlan(conversationIdRef.current, nonce);
      nonceRef.current = null;
      setPendingConfirm(null);
      if (execution.status !== "succeeded") {
        setActivity(activityFromExecution(pending.plan, execution.tool_results, { phase: "failed" }));
        setError("Copilot could not complete this analysis. Please try again.");
        return;
      }
      await finishWithEvidence(pending.plan, pending.userMessage, execution);
    } catch (err) {
      setError(sellerErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [finishWithEvidence, pendingConfirm]);

  const onCancelConfirm = useCallback(() => {
    nonceRef.current = null;
    setPendingConfirm(null);
    setActivity((current) => [
      ...current.filter((item) => item.id !== "confirm"),
      {
        id: "cancelled",
        label: "Amazon lookup was not run",
        status: "done",
      },
    ]);
  }, []);

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    void runTurn(draft);
  }

  const empty = !messages.length && !loading;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Seller Copilot"
        description="Ask a question about your Amazon business. Copilot uses saved analyses first, and asks before looking up a live listing."
      />

      {error ? (
        <Alert variant="destructive">
          <AlertTitle>Copilot could not finish</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="space-y-4">
          <Panel className="p-4 sm:p-5">
            {empty ? (
              <EmptyState
                title="Ask Copilot about a listing"
                description="Start with a saved analysis question, or confirm a fresh ASIN lookup when you need current Amazon data."
              />
            ) : (
              <CopilotMessageList messages={messages} loading={loading} />
            )}
          </Panel>

          <form onSubmit={onSubmit} className="space-y-3">
            <div className="flex flex-wrap gap-2">
              {SUGGESTED_PROMPTS.map((item) => (
                <Button
                  key={item.id}
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={loading}
                  onClick={() => void runTurn(item.message)}
                >
                  {item.label}
                </Button>
              ))}
            </div>
            <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
              <Textarea
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                placeholder="Why is my listing score low?"
                disabled={loading}
                className="min-h-[72px] sm:min-h-[88px]"
              />
              <Button type="submit" disabled={loading || !draft.trim()} className="sm:mb-0.5">
                {loading ? <Loader2 className="animate-spin" /> : <Send />}
                Ask
              </Button>
            </div>
          </form>
        </div>

        <aside className="space-y-4 lg:sticky lg:top-20 lg:self-start">
          <Panel className="space-y-3 p-4">
            <h2 className="text-sm font-semibold">What Copilot understood</h2>
            <p className="text-sm text-muted-foreground">{understood || "Waiting for a question."}</p>
          </Panel>
          <Panel className="space-y-3 p-4">
            <h2 className="text-sm font-semibold">Activity</h2>
            <CopilotActivityTimeline items={activity} />
          </Panel>
          <Panel className="space-y-3 p-4">
            <h2 className="text-sm font-semibold">Evidence</h2>
            <CopilotEvidenceList cards={evidence} />
          </Panel>
        </aside>
      </div>

      {pendingConfirm ? (
        <CopilotConfirmationModal
          summary={pendingConfirm.summary}
          busy={loading}
          onConfirm={() => void onConfirm()}
          onCancel={onCancelConfirm}
        />
      ) : null}
    </div>
  );
}
