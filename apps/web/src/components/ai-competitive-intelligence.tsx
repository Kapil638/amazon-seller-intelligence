"use client";

import type { ReactNode } from "react";

import { Panel, Section } from "@/components/ui/layout";
import { SeverityDot, SeverityLabel } from "@/components/ui/score";
import type { AICompetitiveIntelligence, CompetitivePoint } from "@/lib/types";

function TextList({ items, empty }: { items: string[]; empty: string }) {
  if (!items.length) {
    return <p className="text-sm text-muted-foreground">{empty}</p>;
  }
  return (
    <ul className="space-y-2 text-sm leading-6">
      {items.map((item) => (
        <li key={item} className="flex gap-2">
          <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-muted-foreground/60" />
          <span>{item}</span>
        </li>
      ))}
    </ul>
  );
}

function PointList({ items, empty }: { items: CompetitivePoint[]; empty: string }) {
  if (!items.length) {
    return <p className="text-sm text-muted-foreground">{empty}</p>;
  }
  return (
    <ul className="space-y-4">
      {items.map((item) => (
        <li key={item.title} className="space-y-1">
          <p className="text-sm font-medium">{item.title}</p>
          <p className="text-sm leading-6 text-muted-foreground">{item.evidence}</p>
          <p className="text-sm leading-6">{item.implication}</p>
        </li>
      ))}
    </ul>
  );
}

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="space-y-2 border-t border-border py-5 first:border-t-0 first:pt-0 last:pb-0">
      <h3 className="text-[0.95rem] font-semibold">{title}</h3>
      {children}
    </div>
  );
}

export function AICompetitiveIntelligenceView({
  intelligence,
}: {
  intelligence: AICompetitiveIntelligence;
}) {
  return (
    <Section
      title="AI competitive intelligence"
      eyebrow="Powered by OpenAI"
      description="Interpretation of observed comparison metrics. This does not replace the comparison table."
    >
      <Panel className="p-5 sm:p-6">
        <Block title="Executive assessment">
          <p className="text-sm leading-6">{intelligence.executive_summary}</p>
        </Block>
        <Block title="Competitive position">
          <p className="text-sm leading-6">{intelligence.competitive_position}</p>
        </Block>
        <div className="grid gap-6 border-t border-border py-5 lg:grid-cols-2">
          <div className="space-y-2">
            <h3 className="text-[0.95rem] font-semibold">Your advantages</h3>
            <PointList items={intelligence.target_advantages} empty="No advantages were returned." />
          </div>
          <div className="space-y-2">
            <h3 className="text-[0.95rem] font-semibold">Your disadvantages</h3>
            <PointList items={intelligence.target_disadvantages} empty="No disadvantages were returned." />
          </div>
        </div>
        <Block title="Priority gaps">
          {intelligence.priority_gaps.length ? (
            <ul className="space-y-4">
              {intelligence.priority_gaps.map((gap) => (
                <li key={`${gap.priority}-${gap.dimension}`} className="flex gap-3">
                  <SeverityDot severity={gap.priority} />
                  <div className="min-w-0 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="text-sm font-medium">{gap.dimension}</p>
                      <SeverityLabel severity={gap.priority} />
                    </div>
                    <p className="text-sm leading-6 text-muted-foreground">{gap.evidence}</p>
                    <p className="text-sm leading-6">{gap.recommended_action}</p>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">No priority gaps were returned.</p>
          )}
        </Block>
        <Block title="Competitor observations">
          {intelligence.competitor_observations.length ? (
            <div className="space-y-4">
              {intelligence.competitor_observations.map((item) => (
                <div key={item.asin} className="space-y-2">
                  <p className="font-mono text-xs text-muted-foreground">{item.asin}</p>
                  <TextList items={item.observations} empty="No observations were returned." />
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">No competitor observations were returned.</p>
          )}
        </Block>
        <Block title="Content opportunities">
          <TextList
            items={intelligence.content_opportunities}
            empty="No content opportunities were returned."
          />
        </Block>
        <Block title="Price positioning">
          <p className="text-sm leading-6">{intelligence.price_positioning.observation}</p>
          <p className="text-sm leading-6 text-muted-foreground">{intelligence.price_positioning.caution}</p>
        </Block>
        <Block title="Seller action plan">
          {intelligence.seller_action_plan.length ? (
            <ol className="space-y-3">
              {intelligence.seller_action_plan.map((step) => (
                <li key={step.step} className="flex gap-3 text-sm leading-6">
                  <span className="w-5 shrink-0 tabular-nums text-muted-foreground">{step.step}.</span>
                  <div>
                    <p className="font-medium">{step.action}</p>
                    <p className="text-muted-foreground">{step.evidence}</p>
                    <p>{step.reason}</p>
                  </div>
                </li>
              ))}
            </ol>
          ) : (
            <p className="text-sm text-muted-foreground">No action plan was returned.</p>
          )}
        </Block>
      </Panel>
    </Section>
  );
}
