import { AppShell } from "@/components/app-shell";
import { HistoricalAnalysis } from "@/components/historical-analysis";

export default async function HistoricalAnalysisPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <AppShell current="history">
      <HistoricalAnalysis reportId={id} />
    </AppShell>
  );
}
