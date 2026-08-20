"use client";

import type { ReactNode } from "react";

import { Panel, Section } from "@/components/ui/layout";
import { SeverityDot, SeverityLabel } from "@/components/ui/score";
import type {
  ActionPriority,
  AIListingIntelligenceV2,
  APlusContentInsight,
  BulletContentInsight,
  TitleContentInsight,
} from "@/lib/types";

function TextList({ items, empty }: { items: string[]; empty?: string }) {
  if (!items.length) {
    return empty ? <p className="text-sm text-muted-foreground">{empty}</p> : null;
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

function EvidenceCodes({ codes }: { codes: string[] }) {
  if (!codes.length) {
    return null;
  }
  return (
    <p className="text-xs text-muted-foreground">
      Evidence: {codes.join(", ")}
    </p>
  );
}

function InsightBlock({
  title,
  insight,
  extra,
}: {
  title: string;
  insight: TitleContentInsight | BulletContentInsight | APlusContentInsight;
  extra?: ReactNode;
}) {
  return (
    <div className="space-y-3 rounded-lg border border-border p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="text-sm font-semibold">{title}</h4>
        {"evidence_state" in insight ? (
          <span className="text-xs text-muted-foreground">
            Evidence: {insight.evidence_state.replaceAll("_", " ")}
          </span>
        ) : null}
      </div>
      <p className="text-sm leading-6">{insight.assessment}</p>
      {insight.strengths.length ? (
        <div>
          <p className="mb-1 text-xs text-muted-foreground">Strengths</p>
          <TextList items={insight.strengths} />
        </div>
      ) : null}
      {insight.gaps.length ? (
        <div>
          <p className="mb-1 text-xs text-muted-foreground">Gaps</p>
          <TextList items={insight.gaps} />
        </div>
      ) : null}
      {extra}
    </div>
  );
}

export function AIListingIntelligenceV2View({
  intelligence,
}: {
  intelligence: AIListingIntelligenceV2;
}) {
  const { content_analysis: content, specification_coverage: specs } = intelligence;

  return (
    <Section
      title="AI strategy"
      eyebrow="Content & SEO insights"
      description="Semantic interpretation of Listing Intelligence V2. Deterministic scores stay unchanged. Visual composition was not evaluated."
    >
      <Panel className="p-5 sm:p-6">
        <Block title="Executive assessment">
          <p className="text-sm leading-6">{intelligence.executive_assessment}</p>
        </Block>

        <Block title="Top priorities">
          {intelligence.priority_actions.length ? (
            <ol className="space-y-4">
              {intelligence.priority_actions.map((action, index) => (
                <li key={`${action.area}-${action.issue}`} className="flex gap-3">
                  <span className="w-5 shrink-0 text-sm tabular-nums text-muted-foreground">
                    {index + 1}.
                  </span>
                  <div className="min-w-0 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="text-sm font-medium">{action.area}</p>
                      <SeverityLabel severity={action.priority as ActionPriority} />
                    </div>
                    <p className="text-sm leading-6">{action.issue}</p>
                    <p className="text-sm leading-6 text-muted-foreground">{action.why_it_matters}</p>
                    <p className="text-sm leading-6">{action.recommended_action}</p>
                    <EvidenceCodes codes={action.evidence_codes} />
                  </div>
                </li>
              ))}
            </ol>
          ) : (
            <p className="text-sm text-muted-foreground">No priority actions were returned.</p>
          )}
        </Block>

        <Block title="Content & SEO insights">
          <div className="grid gap-4 lg:grid-cols-2">
            <InsightBlock title="Title" insight={content.title} />
            <InsightBlock
              title="Bullets"
              insight={content.bullets}
              extra={
                content.bullets.seo_readiness_notes.length ? (
                  <div>
                    <p className="mb-1 text-xs text-muted-foreground">SEO readiness</p>
                    <TextList items={content.bullets.seo_readiness_notes} />
                  </div>
                ) : null
              }
            />
            <InsightBlock title="Description" insight={content.description} />
            <InsightBlock title="A+" insight={content.a_plus} />
          </div>
          <div className="mt-4 space-y-3 rounded-lg border border-border p-4">
            <h4 className="text-sm font-semibold">Structure</h4>
            <p className="text-sm leading-6">{content.structure.assessment}</p>
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <p className="mb-1 text-xs text-muted-foreground">Redundancy</p>
                <TextList items={content.structure.redundancy_notes} empty="No redundancy notes." />
              </div>
              <div>
                <p className="mb-1 text-xs text-muted-foreground">Coverage gaps</p>
                <TextList items={content.structure.coverage_gaps} empty="No coverage gaps." />
              </div>
            </div>
          </div>
        </Block>

        <Block title="Specification coverage">
          <div className="grid gap-4 sm:grid-cols-3">
            <div>
              <p className="mb-1 text-xs text-muted-foreground">Already represented</p>
              <TextList items={specs.represented} empty="None noted." />
            </div>
            <div>
              <p className="mb-1 text-xs text-muted-foreground">Missing from customer copy</p>
              <TextList items={specs.missing_from_customer_copy} empty="None noted." />
            </div>
            <div>
              <p className="mb-1 text-xs text-muted-foreground">Not recommended for copy</p>
              <TextList items={specs.not_recommended_for_copy} empty="None noted." />
            </div>
          </div>
        </Block>

        <Block title="Suggested listing copy">
          <dl className="space-y-4 text-sm leading-6">
            <div>
              <dt className="text-xs text-muted-foreground">Suggested title</dt>
              <dd className="font-medium">{intelligence.rewrite_suggestions.suggested_title}</dd>
            </div>
            <div>
              <dt className="mb-2 text-xs text-muted-foreground">Suggested bullets</dt>
              <dd>
                {intelligence.rewrite_suggestions.suggested_bullets.length ? (
                  <ol className="space-y-2">
                    {intelligence.rewrite_suggestions.suggested_bullets.map((bullet, index) => (
                      <li key={bullet} className="flex gap-2">
                        <span className="w-4 shrink-0 tabular-nums text-muted-foreground">{index + 1}.</span>
                        <span>{bullet}</span>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p className="text-muted-foreground">No suggested bullets were returned.</p>
                )}
              </dd>
            </div>
            {intelligence.rewrite_suggestions.optional_description_excerpt ? (
              <div>
                <dt className="text-xs text-muted-foreground">Optional description excerpt</dt>
                <dd>{intelligence.rewrite_suggestions.optional_description_excerpt}</dd>
              </div>
            ) : null}
          </dl>
        </Block>

        <Block title="Seller action plan">
          {intelligence.seller_action_plan.length ? (
            <ol className="space-y-3">
              {intelligence.seller_action_plan.map((step) => (
                <li key={step.step} className="flex gap-3 text-sm leading-6">
                  <SeverityDot severity={step.priority} />
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="font-medium">
                        {step.step}. {step.action}
                      </p>
                      <SeverityLabel severity={step.priority} />
                    </div>
                    <p className="text-muted-foreground">{step.rationale}</p>
                  </div>
                </li>
              ))}
            </ol>
          ) : (
            <p className="text-sm text-muted-foreground">No seller action plan was returned.</p>
          )}
        </Block>

        <Block title="Evidence / confidence notes">
          <TextList
            items={intelligence.confidence_notes}
            empty="No additional confidence notes were returned."
          />
        </Block>
      </Panel>
    </Section>
  );
}
