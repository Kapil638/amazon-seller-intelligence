import { AppNav } from "@/components/app-nav";
import { BulkDueDiligence } from "@/components/bulk-due-diligence";

export default function BulkPage() {
  return (
    <main className="min-h-full">
      <AppNav current="bulk" />
      <div className="px-4 py-12 sm:px-6 lg:px-8">
        <BulkDueDiligence />
      </div>
    </main>
  );
}
