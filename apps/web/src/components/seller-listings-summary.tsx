import { Kpi, Panel } from "@/components/ui/layout";
import type { ListingsSummary } from "@/lib/types";

export function SellerListingsSummaryMetrics({ summary }: { summary: ListingsSummary }) {
  return (
    <Panel className="grid grid-cols-2 gap-4 p-5 sm:grid-cols-3 lg:grid-cols-6">
      <Kpi label="Total listings" value={summary.total_listings} />
      <Kpi
        label="Active"
        value={summary.active_count}
        hint={`${summary.inactive_count} inactive`}
      />
      <Kpi
        label="Buyable"
        value={summary.buyable_count}
        hint={`${summary.not_buyable_count} not buyable`}
      />
      <Kpi
        label="Discoverable"
        value={summary.discoverable_count}
        hint={`${summary.not_discoverable_count} not discoverable`}
      />
      <Kpi
        label="Needs attention"
        value={summary.with_issues_count}
        hint={`${summary.without_issues_count} without issues`}
      />
      <Kpi
        label="Error severity"
        value={summary.issue_severity_error_count}
        hint={`${summary.issue_severity_warning_count} warning · ${summary.issue_severity_info_count} info`}
      />
    </Panel>
  );
}
