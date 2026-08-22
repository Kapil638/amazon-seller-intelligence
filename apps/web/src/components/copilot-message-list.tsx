import type { CopilotSynthesizedResponse } from "@/lib/types";
import { fallbackNotice } from "@/lib/copilot-view";
import { cn } from "@/lib/utils";

export type CopilotChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  response?: CopilotSynthesizedResponse | null;
};

export function CopilotMessageList({
  messages,
  loading,
}: {
  messages: CopilotChatMessage[];
  loading?: boolean;
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
          <AssistantCard key={item.id} message={item} />
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

function AssistantCard({ message }: { message: CopilotChatMessage }) {
  const response = message.response;
  if (!response) {
    return (
      <div className="rounded-lg border border-border bg-surface px-4 py-3 text-sm leading-relaxed">
        {message.content}
      </div>
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
