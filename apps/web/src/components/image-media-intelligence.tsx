"use client";

import type { ReactNode } from "react";

import { Panel, Section } from "@/components/ui/layout";
import { SeverityDot, SeverityLabel } from "@/components/ui/score";
import type { AIImageIntelligence, AIImageIntelligenceResponse, VisualRole } from "@/lib/types";

const ROLE_LABELS: Record<VisualRole, string> = {
  product_only: "Product only",
  feature: "Feature",
  benefit: "Benefit",
  lifestyle: "Lifestyle",
  dimensions: "Dimensions",
  how_to_use: "How to use",
  packaging: "Packaging",
  comparison: "Comparison",
  detail_closeup: "Detail / close-up",
  other: "Other",
};

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

function ImageIds({ ids }: { ids: string[] }) {
  if (!ids.length) {
    return null;
  }
  return <p className="text-xs text-muted-foreground">Images: {ids.join(", ")}</p>;
}

function RoleList({ roles }: { roles: VisualRole[] }) {
  if (!roles.length) {
    return <p className="text-sm text-muted-foreground">None noted.</p>;
  }
  return (
    <p className="text-sm leading-6">
      {roles.map((role) => ROLE_LABELS[role] ?? role).join(", ")}
    </p>
  );
}

export function ImageMediaIntelligenceView({
  intelligence,
  meta,
}: {
  intelligence: AIImageIntelligence;
  meta?: AIImageIntelligenceResponse["meta"];
}) {
  return (
    <Section
      title="Image & media intelligence"
      eyebrow="Optional multimodal AI"
      description="Visual interpretation of listing images and A+ media. This does not change listing-quality scores and does not generate images."
    >
      <Panel className="p-5 sm:p-6">
        {meta ? (
          <p className="mb-4 text-xs text-muted-foreground">
            Analyzed {meta.images_selected} of {meta.images_available} available images
            {meta.images_skipped ? `; ${meta.images_skipped} skipped` : ""}. Cached results are reused.
          </p>
        ) : null}

        <Block title="Executive assessment">
          <p className="text-sm leading-6">{intelligence.executive_assessment}</p>
        </Block>

        <Block title="Main image">
          <p className="text-sm leading-6">{intelligence.main_image_analysis.assessment}</p>
          {intelligence.main_image_analysis.product_visibility ? (
            <p className="text-sm text-muted-foreground">
              Visibility: {intelligence.main_image_analysis.product_visibility}
            </p>
          ) : null}
          <TextList items={intelligence.main_image_analysis.strengths} />
          <TextList items={intelligence.main_image_analysis.concerns} />
          <ImageIds ids={intelligence.main_image_analysis.image_ids} />
        </Block>

        <Block title="Gallery strategy">
          <p className="text-sm leading-6">{intelligence.gallery_analysis.assessment}</p>
          <p className="text-xs text-muted-foreground">Observed roles</p>
          <RoleList roles={intelligence.gallery_analysis.observed_roles} />
          <p className="pt-2 text-xs text-muted-foreground">Coverage opportunities</p>
          <TextList
            items={intelligence.gallery_analysis.coverage_opportunities}
            empty="No additional gallery roles were suggested."
          />
        </Block>

        <Block title="Visual strengths">
          <TextList items={intelligence.visual_strengths} empty="No visual strengths were returned." />
        </Block>

        <Block title="Priority improvements">
          {intelligence.priority_improvements.length ? (
            <ul className="space-y-4">
              {intelligence.priority_improvements.map((item) => (
                <li key={`${item.priority}-${item.issue}`} className="flex gap-3">
                  <SeverityDot severity={item.priority} />
                  <div className="min-w-0 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="text-sm font-medium">{item.issue}</p>
                      <SeverityLabel severity={item.priority} />
                    </div>
                    <p className="text-sm leading-6 text-muted-foreground">{item.why_it_matters}</p>
                    <p className="text-sm leading-6">{item.recommended_action}</p>
                    <ImageIds ids={item.image_ids} />
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">No priority image improvements were returned.</p>
          )}
        </Block>

        <Block title="A+ visual intelligence">
          <p className="text-xs text-muted-foreground">
            Evidence: {intelligence.a_plus_visual_analysis.evidence_state.replaceAll("_", " ")}
          </p>
          <p className="text-sm leading-6">{intelligence.a_plus_visual_analysis.assessment}</p>
          <TextList items={intelligence.a_plus_visual_analysis.strengths} />
          <TextList items={intelligence.a_plus_visual_analysis.gaps} />
          <ImageIds ids={intelligence.a_plus_visual_analysis.image_ids} />
        </Block>

        <Block title="Brand Story">
          <p className="text-xs text-muted-foreground">
            Evidence: {intelligence.brand_story_analysis.evidence_state.replaceAll("_", " ")}
          </p>
          <p className="text-sm leading-6">{intelligence.brand_story_analysis.assessment}</p>
          <TextList items={intelligence.brand_story_analysis.strengths} />
          <TextList items={intelligence.brand_story_analysis.gaps} />
        </Block>

        <Block title="Media role coverage">
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <p className="mb-1 text-xs text-muted-foreground">Observed</p>
              <RoleList roles={intelligence.media_role_coverage.observed} />
            </div>
            <div>
              <p className="mb-1 text-xs text-muted-foreground">Not observed in supplied images</p>
              <RoleList roles={intelligence.media_role_coverage.not_observed} />
            </div>
          </div>
          <TextList items={intelligence.media_role_coverage.notes} />
        </Block>

        <Block title="Redundancy / repetition">
          <TextList items={intelligence.redundancy_analysis} empty="No redundancy notes were returned." />
        </Block>

        <Block title="Recommended image plan">
          {intelligence.recommended_image_plan.length ? (
            <ol className="space-y-3">
              {intelligence.recommended_image_plan.map((step) => (
                <li key={step.step} className="text-sm leading-6">
                  <p className="font-medium">
                    {step.step}. {step.slot}
                  </p>
                  <p>{step.purpose}</p>
                  <p className="text-muted-foreground">{step.grounded_in}</p>
                </li>
              ))}
            </ol>
          ) : (
            <p className="text-sm text-muted-foreground">No image plan was returned.</p>
          )}
        </Block>

        {intelligence.image_findings.length ? (
          <Block title="Image findings">
            <ul className="space-y-3">
              {intelligence.image_findings.map((finding) => (
                <li key={`${finding.evidence_type}-${finding.observation}`} className="text-sm leading-6">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="font-medium">{finding.evidence_type}</p>
                    <SeverityLabel severity={finding.severity} />
                  </div>
                  <p>{finding.observation}</p>
                  <p className="text-muted-foreground">{finding.recommendation}</p>
                  <ImageIds ids={finding.image_ids} />
                </li>
              ))}
            </ul>
          </Block>
        ) : null}

        <Block title="Confidence / evidence limitations">
          <TextList
            items={intelligence.confidence_notes}
            empty="No additional confidence notes were returned."
          />
        </Block>
      </Panel>
    </Section>
  );
}
