import { AppShell } from "@/components/app-shell";
import { AnalysisHistory } from "@/components/analysis-history";

export default function HistoryPage() {
  return (
    <AppShell current="history">
      <AnalysisHistory />
    </AppShell>
  );
}
