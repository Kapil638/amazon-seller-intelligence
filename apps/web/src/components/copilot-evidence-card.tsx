import Link from "next/link";

import { Button } from "@/components/ui/button";
import type { EvidenceCardModel } from "@/lib/copilot-view";

export function CopilotEvidenceCard({ card }: { card: EvidenceCardModel }) {
  return (
    <article className="rounded-lg border border-border bg-surface p-4 shadow-[var(--shadow-sm)]">
      <p className="text-xs text-muted-foreground">{card.source}</p>
      <h3 className="mt-1 text-sm font-medium">{card.title}</h3>
      <p className="mt-1 text-lg font-semibold tabular-nums tracking-tight">{card.value}</p>
      {card.date ? <p className="mt-1 text-xs text-muted-foreground">{card.date}</p> : null}
      {card.href ? (
        <Button asChild variant="link" size="sm" className="mt-2 h-auto px-0">
          <Link href={card.href}>{card.hrefLabel || "Open saved report"}</Link>
        </Button>
      ) : null}
    </article>
  );
}

export function CopilotEvidenceList({ cards }: { cards: EvidenceCardModel[] }) {
  if (!cards.length) {
    return <p className="text-sm text-muted-foreground">No evidence cards for this turn yet.</p>;
  }
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {cards.map((card) => (
        <CopilotEvidenceCard key={card.id} card={card} />
      ))}
    </div>
  );
}
