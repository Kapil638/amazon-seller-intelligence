"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import type { ActionPriority, AICompetitiveIntelligence, CompetitivePoint } from "@/lib/types";

const PRIORITY_STYLES: Record<ActionPriority, string> = {
  high: "border-transparent bg-destructive text-destructive-foreground",
  medium: "border-transparent bg-amber-100 text-amber-900",
  low: "border-transparent bg-secondary text-secondary-foreground",
};

function TextList({ items, empty }: { items: string[]; empty: string }) {
  if (!items.length) {
    return <p className="text-sm text-muted-foreground">{empty}</p>;
  }
  return (
    <ul className="list-disc space-y-2 pl-5 text-sm leading-6 text-foreground">
      {items.map((item) => (
        <li key={item}>{item}</li>
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
          <p className="text-sm leading-6 text-foreground">{item.implication}</p>
        </li>
      ))}
    </ul>
  );
}

export function AICompetitiveIntelligenceView({
  intelligence,
}: {
  intelligence: AICompetitiveIntelligence;
}) {
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader className="space-y-2">
          <p className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">
            AI Competitive Intelligence
          </p>
          <CardTitle className="text-2xl">Executive Summary</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm leading-6 text-foreground">{intelligence.executive_summary}</p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Competitive Position</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm leading-6 text-foreground">{intelligence.competitive_position}</p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Your Advantages</CardTitle>
        </CardHeader>
        <CardContent>
          <PointList items={intelligence.target_advantages} empty="No advantages were returned." />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Your Disadvantages</CardTitle>
        </CardHeader>
        <CardContent>
          <PointList
            items={intelligence.target_disadvantages}
            empty="No disadvantages were returned."
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Priority Gaps</CardTitle>
        </CardHeader>
        <CardContent>
          {intelligence.priority_gaps.length ? (
            <ul className="space-y-4">
              {intelligence.priority_gaps.map((gap) => (
                <li key={`${gap.priority}-${gap.dimension}`} className="space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge className={cn("uppercase", PRIORITY_STYLES[gap.priority])}>
                      {gap.priority}
                    </Badge>
                    <p className="text-sm font-medium">{gap.dimension}</p>
                  </div>
                  <p className="text-sm leading-6 text-muted-foreground">{gap.evidence}</p>
                  <p className="text-sm leading-6 text-foreground">{gap.recommended_action}</p>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">No priority gaps were returned.</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Competitor Observations</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {intelligence.competitor_observations.length ? (
            intelligence.competitor_observations.map((item) => (
              <div key={item.asin} className="space-y-2">
                <p className="font-mono text-sm">{item.asin}</p>
                <TextList items={item.observations} empty="No observations were returned." />
              </div>
            ))
          ) : (
            <p className="text-sm text-muted-foreground">No competitor observations were returned.</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Content Opportunities</CardTitle>
        </CardHeader>
        <CardContent>
          <TextList
            items={intelligence.content_opportunities}
            empty="No content opportunities were returned."
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Price Positioning</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          <p className="text-sm leading-6 text-foreground">
            {intelligence.price_positioning.observation}
          </p>
          <p className="text-sm leading-6 text-muted-foreground">
            {intelligence.price_positioning.caution}
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Seller Action Plan</CardTitle>
        </CardHeader>
        <CardContent>
          {intelligence.seller_action_plan.length ? (
            <ol className="space-y-4">
              {intelligence.seller_action_plan.map((step) => (
                <li key={step.step} className="space-y-1">
                  <p className="text-sm font-medium">
                    {step.step}. {step.action}
                  </p>
                  <p className="text-sm leading-6 text-muted-foreground">{step.evidence}</p>
                  <p className="text-sm leading-6 text-foreground">{step.reason}</p>
                </li>
              ))}
            </ol>
          ) : (
            <p className="text-sm text-muted-foreground">No action plan was returned.</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
