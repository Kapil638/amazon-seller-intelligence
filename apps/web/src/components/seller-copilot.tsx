"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Send } from "lucide-react";

import { CopilotActivityTimeline } from "@/components/copilot-activity-timeline";
import { CopilotConfirmationModal } from "@/components/copilot-confirmation-modal";
import { CopilotEvidenceList } from "@/components/copilot-evidence-card";
import { CopilotMessageList, type CopilotChatMessage } from "@/components/copilot-message-list";
import { SellerListingsMarketplaceSelector } from "@/components/seller-listings-marketplace-selector";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { PageHeader, Panel } from "@/components/ui/layout";
import { Textarea } from "@/components/ui/textarea";
import {
  AmazonConnectionError,
  confirmCopilotPlan,
  createCopilotConversation,
  executeCopilotPlan,
  fetchAmazonConnection,
  fetchCopilotConversation,
  fetchListingsSummary,
  fetchOrdersSummary,
  planCopilotTurn,
  synthesizeCopilot,
} from "@/lib/api";
import { CANONICAL_MARKETPLACE_ID, marketplaceDisplayName } from "@/lib/seller-listings-view";
import type {
  AmazonSellerMarketplace,
  CopilotEvidenceEnvelope,
  CopilotExecutionResult,
  CopilotPlan,
  CopilotSynthesizedResponse,
  ListingsSummary,
  OrdersSummary,
} from "@/lib/types";
import {
  ANALYZE_ASIN_PROMPT,
  COPILOT_EMPTY_DESCRIPTION,
  COPILOT_EMPTY_HEADING,
  DEFAULT_PERIOD_DAYS,
  PERIOD_OPTIONS,
  PRODUCT_RESEARCH_HEADING,
  SKILL_SUGGESTIONS,
  SUGGESTED_PROMPTS,
  activityFromExecution,
  activityFromPlan,
  executePayload,
  evidenceCardsFromEnvelopes,
  extractSkillEvidence,
  intentLabel,
  periodLabel,
  sellerErrorMessage,
  type ActivityItem,
  type EvidenceCardModel,
} from "@/lib/copilot-view";

type PendingConfirm = {
  plan: CopilotPlan;
  userMessage: string;
  summary: string;
};

type AddressableMarketplace = AmazonSellerMarketplace & { id: string };

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

  const [marketplaces, setMarketplaces] = useState<AddressableMarketplace[]>([]);
  const [participationId, setParticipationId] = useState<string | null>(null);
  const [marketplacesLoading, setMarketplacesLoading] = useState(true);
  const [periodDays, setPeriodDays] = useState<number>(DEFAULT_PERIOD_DAYS);
  const [listingsSummary, setListingsSummary] = useState<ListingsSummary | null>(null);
  const [ordersSummary, setOrdersSummary] = useState<OrdersSummary | null>(null);
  const participationIdRef = useRef<string | null>(null);
  participationIdRef.current = participationId;
  const periodDaysRef = useRef<number>(DEFAULT_PERIOD_DAYS);
  periodDaysRef.current = periodDays;

  useEffect(() => {
    let cancelled = false;
    fetchAmazonConnection()
      .then((overview) => {
        if (cancelled) return;
        const addressable = (overview.marketplaces ?? []).filter((m): m is AddressableMarketplace => Boolean(m.id));
        setMarketplaces(addressable);
        if (addressable.length > 0) {
          const canonical = addressable.find((m) => m.marketplace_id === CANONICAL_MARKETPLACE_ID);
          setParticipationId((canonical ?? addressable[0]).id);
        }
      })
      .catch(() => {
        // Copilot still works without a marketplace context — launch
        // skills stay disabled and explain why until one is connected.
      })
      .finally(() => {
        if (!cancelled) setMarketplacesLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!participationId) {
      setListingsSummary(null);
      setOrdersSummary(null);
      return;
    }
    let cancelled = false;
    Promise.allSettled([fetchListingsSummary(participationId), fetchOrdersSummary(participationId)]).then(
      ([listingsResult, ordersResult]) => {
        if (cancelled) return;
        setListingsSummary(listingsResult.status === "fulfilled" ? listingsResult.value : null);
        setOrdersSummary(ordersResult.status === "fulfilled" ? ordersResult.value : null);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [participationId]);

  const appendAssistant = useCallback(
    (response: CopilotSynthesizedResponse, evidenceEnvelopes: CopilotEvidenceEnvelope[], originatingQuestion: string) => {
      setMessages((current) => [
        ...current,
        {
          id: `assistant-${Date.now()}`,
          role: "assistant",
          content: response.summary,
          response,
          skillEvidence: extractSkillEvidence(evidenceEnvelopes),
          originatingQuestion,
        },
      ]);
    },
    [],
  );

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
      appendAssistant(response, execution.evidence, userMessage);
      setEvidence(evidenceCardsFromEnvelopes(execution.evidence));
      setActivity(activityFromExecution(plan, execution.tool_results, { phase: "answered" }));
    },
    [appendAssistant],
  );

  // Every submission — a launch-skill card, a "Product research" chip, or
  // free-form text — goes through this exact same path. The prompt text
  // itself never authorizes or routes anything: the backend planner
  // independently infers intent and validates any tool call against its
  // own registered schema (see app/copilot/planner/validator.py). This
  // function only ever attaches the seller's *currently selected* scope
  // — never a guessed or stale one.
  const runTurn = useCallback(
    async (rawMessage: string, options?: { forceRefresh?: boolean }) => {
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
        const plan = await planCopilotTurn(conversationId, userMessage, {
          marketplaceParticipationId: participationIdRef.current,
          periodDays: periodDaysRef.current,
          forceRefresh: options?.forceRefresh,
        });
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
          appendAssistant(response, [], userMessage);
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
  const selectedMarketplace = marketplaces.find((m) => m.id === participationId) || null;
  const skillsDisabled = loading || marketplacesLoading || !participationId;

  return (
    <div className="space-y-6">
      <PageHeader title="Copilot" />

      <Panel className="flex flex-wrap items-end gap-4 p-4">
        {!marketplacesLoading && marketplaces.length > 1 ? (
          <div className="max-w-xs">
            <SellerListingsMarketplaceSelector
              marketplaces={marketplaces}
              selectedId={participationId}
              onChange={setParticipationId}
              disabled={loading}
            />
          </div>
        ) : null}
        <div className="min-w-0">
          <label htmlFor="copilot-period" className="text-xs font-medium text-muted-foreground">
            Analysis period
          </label>
          <select
            id="copilot-period"
            className="mt-1 flex h-10 w-full min-w-[10rem] rounded-md border border-input bg-surface px-3 py-2 text-sm transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
            value={periodDays}
            disabled={loading}
            onChange={(event) => setPeriodDays(Number(event.target.value))}
          >
            {PERIOD_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
        <p className="text-sm text-muted-foreground">
          {selectedMarketplace ? (
            <>
              Showing <span className="font-medium text-foreground">{marketplaceDisplayName(selectedMarketplace)}</span>{" "}
              · {periodLabel(periodDays)}
            </>
          ) : marketplacesLoading ? (
            "Loading your connected marketplaces…"
          ) : (
            "Connect an Amazon marketplace to use the launch skills below."
          )}
        </p>
      </Panel>

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
              <div className="space-y-6">
                <div>
                  <h2 className="text-lg font-semibold leading-tight">{COPILOT_EMPTY_HEADING}</h2>
                  <p className="mt-1 text-sm text-muted-foreground">{COPILOT_EMPTY_DESCRIPTION}</p>
                </div>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {SKILL_SUGGESTIONS.map((skill) => (
                    <button
                      key={skill.id}
                      type="button"
                      disabled={skillsDisabled}
                      onClick={() => void runTurn(skill.question)}
                      className="rounded-md border border-border p-3 text-left text-sm transition-colors hover:bg-surface-muted disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <p className="font-medium">{skill.question}</p>
                      <p className="mt-1 text-xs text-muted-foreground">{skill.explanation}</p>
                    </button>
                  ))}
                </div>
                <div>
                  <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    {PRODUCT_RESEARCH_HEADING}
                  </h3>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Saved-analysis and ASIN research tools — based on public marketplace data, not your
                    synchronized seller data.
                  </p>
                  <div className="mt-2 flex flex-wrap gap-2">
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
                </div>
              </div>
            ) : (
              <CopilotMessageList
                messages={messages}
                loading={loading}
                onRecompute={(question) => void runTurn(question, { forceRefresh: true })}
                recomputeDisabled={loading}
              />
            )}
          </Panel>

          <form onSubmit={onSubmit} className="space-y-3">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
              <Textarea
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                placeholder="Which listings should I fix first?"
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
          <Panel className="space-y-2 p-4 text-sm">
            <h2 className="text-sm font-semibold">Data freshness</h2>
            <p className="text-muted-foreground">
              Listings: {listingsSummary ? listingsSummary.sync.status.replace(/_/g, " ") : "—"}
            </p>
            <p className="text-muted-foreground">
              Orders: {ordersSummary ? ordersSummary.sync.status.replace(/_/g, " ") : "—"}
            </p>
          </Panel>
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
