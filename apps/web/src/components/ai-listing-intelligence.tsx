"use client";

import type { ReactNode } from "react";

import { Panel, Section } from "@/components/ui/layout";
import { SeverityDot, SeverityLabel } from "@/components/ui/score";
import type { ActionPriority, AIListingIntelligence } from "@/lib/types";

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

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="space-y-2 border-t border-border py-5 first:border-t-0 first:pt-0 last:pb-0">
      <h3 className="text-[0.95rem] font-semibold">{title}</h3>
      {children}
    </div>
  );
}

export function AIListingIntelligenceView({
  intelligence,
}: {
  intelligence: AIListingIntelligence;
}) {
  return (
    <Section
      title="AI strategy"
      eyebrow="Powered by OpenAI"
      description="Strategic interpretation of the deterministic listing analysis. This is not a replacement for scores."
    >
      <Panel className="p-5 sm:p-6">
        <Block title="Executive assessment">
          <p className="text-sm leading-6">{intelligence.executive_summary}</p>
        </Block>

        <Block title="Priority opportunities">
          {intelligence.priority_actions.length ? (
            <ul className="space-y-4">
              {intelligence.priority_actions.map((action) => (
                <li key={`${action.priority}-${action.title}`} className="flex gap-3">
                  <SeverityDot severity={action.priority} />
                  <div className="min-w-0 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="text-sm font-medium">{action.title}</p>
                      <SeverityLabel severity={action.priority as ActionPriority} />
                    </div>
                    <p className="text-sm leading-6 text-muted-foreground">{action.reason}</p>
                    <p className="text-sm leading-6">{action.recommended_action}</p>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">No priority actions were returned.</p>
          )}
        </Block>

        <div className="grid gap-6 border-t border-border py-5 lg:grid-cols-2">
          <div className="space-y-2">
            <h3 className="text-[0.95rem] font-semibold">Strengths</h3>
            <TextList items={intelligence.strengths} empty="No strengths were returned." />
          </div>
          <div className="space-y-2">
            <h3 className="text-[0.95rem] font-semibold">Weaknesses</h3>
            <TextList items={intelligence.weaknesses} empty="No weaknesses were returned." />
          </div>
        </div>

        <Block title="Suggested title">
          <dl className="space-y-3 text-sm leading-6">
            <div>
              <dt className="text-xs text-muted-foreground">Current</dt>
              <dd>{intelligence.title_recommendation.current_title || "Not available"}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Recommended</dt>
              <dd className="font-medium">{intelligence.title_recommendation.suggested_title}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Why</dt>
              <dd className="text-muted-foreground">{intelligence.title_recommendation.rationale}</dd>
            </div>
          </dl>
        </Block>

        <Block title="Suggested bullet points">
          {intelligence.bullet_recommendations.length ? (
            <div className="space-y-4">
              {intelligence.bullet_recommendations.map((item, index) => (
                <div key={`${item.suggested}-${index}`} className="space-y-1 text-sm leading-6">
                  <p className="text-xs text-muted-foreground">Bullet {index + 1}</p>
                  <p>
                    <span className="text-muted-foreground">Current: </span>
                    {item.current || "Not available"}
                  </p>
                  <p>
                    <span className="text-muted-foreground">Recommended: </span>
                    {item.suggested}
                  </p>
                  <p className="text-muted-foreground">{item.rationale}</p>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">No bullet recommendations were returned.</p>
          )}
        </Block>

        <Block title="Listing improvements">
          <div className="grid gap-6 lg:grid-cols-2">
            <div>
              <p className="mb-2 text-xs text-muted-foreground">Positioning</p>
              <TextList
                items={intelligence.positioning_opportunities}
                empty="No positioning opportunities were returned."
              />
            </div>
            <div>
              <p className="mb-2 text-xs text-muted-foreground">Conversion</p>
              <TextList
                items={intelligence.conversion_opportunities}
                empty="No conversion opportunities were returned."
              />
            </div>
          </div>
        </Block>

        <Block title="Risks and cautions">
          <TextList items={intelligence.risks_and_cautions} empty="No risks or cautions were returned." />
        </Block>

        <Block title="Seller action plan">
          {intelligence.seller_action_plan.length ? (
            <ol className="space-y-3">
              {intelligence.seller_action_plan.map((step) => (
                <li key={step.step} className="flex gap-3 text-sm leading-6">
                  <span className="w-5 shrink-0 tabular-nums text-muted-foreground">{step.step}.</span>
                  <div>
                    <p className="font-medium">{step.action}</p>
                    <p className="text-muted-foreground">{step.reason}</p>
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
