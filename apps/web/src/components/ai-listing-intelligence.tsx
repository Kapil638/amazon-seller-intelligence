"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import type { ActionPriority, AIListingIntelligence } from "@/lib/types";

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

export function AIListingIntelligenceView({
  intelligence,
}: {
  intelligence: AIListingIntelligence;
}) {
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader className="space-y-2">
          <p className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">
            AI Recommendations
          </p>
          <CardTitle className="text-2xl">Strategic interpretation</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm leading-6 text-foreground">{intelligence.executive_summary}</p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Top Priorities</CardTitle>
        </CardHeader>
        <CardContent>
          {intelligence.priority_actions.length ? (
            <ul className="space-y-4">
              {intelligence.priority_actions.map((action) => (
                <li key={`${action.priority}-${action.title}`} className="space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge className={cn("uppercase", PRIORITY_STYLES[action.priority])}>
                      {action.priority}
                    </Badge>
                    <p className="text-sm font-medium">{action.title}</p>
                  </div>
                  <p className="text-sm leading-6 text-muted-foreground">{action.reason}</p>
                  <p className="text-sm leading-6 text-foreground">{action.recommended_action}</p>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">No priority actions were returned.</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Strengths</CardTitle>
        </CardHeader>
        <CardContent>
          <TextList items={intelligence.strengths} empty="No strengths were returned." />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Weaknesses</CardTitle>
        </CardHeader>
        <CardContent>
          <TextList items={intelligence.weaknesses} empty="No weaknesses were returned." />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Suggested Title</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4 text-sm leading-6">
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Current</p>
            <p>{intelligence.title_recommendation.current_title || "Not available"}</p>
          </div>
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Recommended
            </p>
            <p>{intelligence.title_recommendation.suggested_title}</p>
          </div>
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Why</p>
            <p>{intelligence.title_recommendation.rationale}</p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Suggested Bullet Points</CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          {intelligence.bullet_recommendations.length ? (
            intelligence.bullet_recommendations.map((item, index) => (
              <div key={`${item.suggested}-${index}`} className="space-y-2 text-sm leading-6">
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  Bullet {index + 1}
                </p>
                <p>
                  <span className="text-muted-foreground">Current: </span>
                  {item.current || "Not available"}
                </p>
                <p>
                  <span className="text-muted-foreground">Recommended: </span>
                  {item.suggested}
                </p>
                <p>
                  <span className="text-muted-foreground">Why: </span>
                  {item.rationale}
                </p>
              </div>
            ))
          ) : (
            <p className="text-sm text-muted-foreground">No bullet recommendations were returned.</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Positioning Opportunities</CardTitle>
        </CardHeader>
        <CardContent>
          <TextList
            items={intelligence.positioning_opportunities}
            empty="No positioning opportunities were returned."
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Conversion Opportunities</CardTitle>
        </CardHeader>
        <CardContent>
          <TextList
            items={intelligence.conversion_opportunities}
            empty="No conversion opportunities were returned."
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Risks / Cautions</CardTitle>
        </CardHeader>
        <CardContent>
          <TextList
            items={intelligence.risks_and_cautions}
            empty="No risks or cautions were returned."
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Seller Action Plan</CardTitle>
        </CardHeader>
        <CardContent>
          {intelligence.seller_action_plan.length ? (
            <ol className="list-decimal space-y-3 pl-5 text-sm leading-6">
              {intelligence.seller_action_plan.map((step) => (
                <li key={step.step}>
                  <p className="font-medium">{step.action}</p>
                  <p className="text-muted-foreground">{step.reason}</p>
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
