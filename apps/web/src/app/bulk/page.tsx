import { AppShell } from "@/components/app-shell";
import { BulkDueDiligence } from "@/components/bulk-due-diligence";

export default function BulkPage() {
  return (
    <AppShell current="bulk">
      <BulkDueDiligence />
    </AppShell>
  );
}
