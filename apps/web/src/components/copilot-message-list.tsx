import Link from "next/link";

import type { CopilotSkillEvidence, CopilotSynthesizedResponse } from "@/lib/types";
import { describeSkillFreshness, fallbackNotice } from "@/lib/copilot-view";
import { cn } from "@/lib/utils";

export type CopilotChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  response?: CopilotSynthesizedResponse | null;
  skillEvidence?: CopilotSkillEvidence | null;
  // The exact question that produced this answer — carried only so a
  // "Recompute from saved data" click can resubmit the identical
  // question with force_refresh, without guessing or reusing whatever
  // is currently in the draft textbox.
  originatingQuestion?: string;
};

export function CopilotMessageList({
  messages,
  loading,
  onRecompute,
  recomputeDisabled,
}: {
  messages: CopilotChatMessage[];
  loading?: boolean;
  onRecompute?: (question: string) => void;
  recomputeDisabled?: boolean;
}) {
  if (!messages.length && !loading) {
    return null;
  }
  return (
    <div className="space-y-4" aria-live="polite">
      {messages.map((item) =>
        item.role === "user" ? (
          <UserBubble key={item.id} content={item.content} />
        ) : (
          <AssistantCard key={item.id} message={item} onRecompute={onRecompute} recomputeDisabled={recomputeDisabled} />
        ),
      )}
      {loading ? (
        <div className="rounded-lg border border-dashed border-border bg-surface px-4 py-3 text-sm text-muted-foreground">
          Copilot is working through trusted tools…
        </div>
      ) : null}
    </div>
  );
}

function UserBubble({ content }: { content: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] rounded-lg bg-primary px-3.5 py-2.5 text-sm text-primary-foreground">
        {content}
      </div>
    </div>
  );
}

function AssistantCard({
  message,
  onRecompute,
  recomputeDisabled,
}: {
  message: CopilotChatMessage;
  onRecompute?: (question: string) => void;
  recomputeDisabled?: boolean;
}) {
  const response = message.response;
  if (!response) {
    return (
      <div className="rounded-lg border border-border bg-surface px-4 py-3 text-sm leading-relaxed">
        {message.content}
      </div>
    );
  }
  if (message.skillEvidence) {
    return (
      <SkillAnswerCard
        response={response}
        evidence={message.skillEvidence}
        onRecompute={
          onRecompute && message.originatingQuestion ? () => onRecompute(message.originatingQuestion!) : undefined
        }
        recomputeDisabled={recomputeDisabled}
      />
    );
  }
  const notice = fallbackNotice(response);
  return (
    <div className="space-y-4 rounded-lg border border-border bg-surface p-4 shadow-[var(--shadow-sm)]">
      {notice ? <p className="text-xs text-muted-foreground">{notice}</p> : null}
      <section>
        <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Summary</h3>
        <p className="mt-1 text-sm leading-relaxed">{response.summary}</p>
      </section>
      <section>
        <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Key findings</h3>
        {response.findings.length ? (
          <ul className="mt-1 list-disc space-y-1 pl-5 text-sm leading-relaxed">
            {response.findings.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-sm text-muted-foreground">No evidence-backed findings were available.</p>
        )}
      </section>
      <section>
        <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Recommended actions</h3>
        {response.recommendations.length ? (
          <ul className="mt-1 list-disc space-y-1 pl-5 text-sm leading-relaxed">
            {response.recommendations.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-sm text-muted-foreground">No evidence-backed actions were available.</p>
        )}
      </section>
      {response.citations.length ? (
        <section>
          <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Evidence</h3>
          <ul className="mt-1 space-y-1 text-sm text-muted-foreground">
            {response.citations.map((item) => (
              <li key={`${item.evidence_id}-${item.claim_key}`}>
                {item.claim_key} · {item.label}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {response.unknowns.length ? (
        <p className={cn("text-xs text-muted-foreground")}>
          Not in this evidence: {response.unknowns.join("; ")}
        </p>
      ) : null}
    </div>
  );
}

const CONFIDENCE_LABEL: Record<string, string> = {
  high: "High confidence",
  medium: "Medium confidence — a newer sync has not finished yet",
  low: "Low confidence",
  insufficient_data: "Not enough data yet",
};

// The six-section answer contract for a launch-skill response: Answer,
// Evidence, Data freshness, Suggested next step, Limitations, and View
// supporting Listings/Orders links — built from typed evidence, not
// parsed out of model text, and using only customer language (no tool
// names, run ids, or internal identifiers).
function SkillAnswerCard({
  response,
  evidence,
  onRecompute,
  recomputeDisabled,
}: {
  response: CopilotSynthesizedResponse;
  evidence: CopilotSkillEvidence;
  onRecompute?: () => void;
  recomputeDisabled?: boolean;
}) {
  const freshness = describeSkillFreshness(evidence);
  // The deterministic template always puts the freshness sentence first
  // in `findings` (see synthesis/validator.py's `_freshness_finding`) —
  // it now has its own dedicated section below, so it is skipped here to
  // avoid saying the same thing twice.
  const evidenceFindings = response.findings.filter((_, index) => index !== 0 || response.findings.length === 1);

  return (
    <div className="space-y-4 rounded-lg border border-border bg-surface p-4 shadow-[var(--shadow-sm)]">
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded-full bg-surface-muted px-2.5 py-0.5 text-xs font-medium text-muted-foreground">
          {CONFIDENCE_LABEL[evidence.confidence] || "Confidence unknown"}
        </span>
      </div>
      <section>
        <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Answer</h3>
        <p className="mt-1 text-sm leading-relaxed">{response.summary}</p>
      </section>
      <section>
        <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Evidence</h3>
        {evidenceFindings.length ? (
          <ul className="mt-1 list-disc space-y-1 pl-5 text-sm leading-relaxed">
            {evidenceFindings.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-sm text-muted-foreground">No supporting evidence was available.</p>
        )}
      </section>
      <section>
        <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Data freshness</h3>
        <p className="mt-1 text-sm text-muted-foreground">{freshness.line || "Freshness is unknown."}</p>
        {freshness.warning ? (
          <p className="mt-1 text-sm text-amber-700 dark:text-amber-500">{freshness.warning}</p>
        ) : null}
        {onRecompute ? (
          <button
            type="button"
            onClick={onRecompute}
            disabled={recomputeDisabled}
            className="mt-2 text-sm text-primary underline underline-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Recompute from saved data
          </button>
        ) : null}
      </section>
      <section>
        <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Suggested next step</h3>
        {response.recommendations.length ? (
          <ul className="mt-1 list-disc space-y-1 pl-5 text-sm leading-relaxed">
            {response.recommendations.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-sm text-muted-foreground">No specific action is needed right now.</p>
        )}
      </section>
      {evidence.limitations.length ? (
        <section>
          <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Limitations</h3>
          <ul className="mt-1 list-disc space-y-1 pl-5 text-sm text-muted-foreground">
            {evidence.limitations.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </section>
      ) : null}
      {evidence.deep_links.length ? (
        <section>
          <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            View supporting data
          </h3>
          <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
            {evidence.deep_links.map((link) => (
              <Link key={link.href} href={link.href} className="text-sm text-primary underline underline-offset-2">
                {link.label}
              </Link>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}
