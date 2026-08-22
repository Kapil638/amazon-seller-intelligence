import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/layout";

export function CopilotConfirmationModal({
  summary,
  busy,
  onConfirm,
  onCancel,
}: {
  summary: string;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4 sm:items-center"
      role="dialog"
      aria-modal="true"
      aria-labelledby="copilot-confirm-title"
    >
      <Panel className="w-full max-w-md space-y-4 p-5">
        <h3 id="copilot-confirm-title" className="text-base font-semibold">
          Fresh Amazon lookup required
        </h3>
        <p className="text-sm leading-relaxed text-muted-foreground">
          {summary ||
            "You don’t have a saved analysis for this ASIN. Continuing will look up the listing and use product credits."}
        </p>
        <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
          <li>Fetch current product data</li>
          <li>Consume lookup credits</li>
        </ul>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" disabled={busy} onClick={onCancel}>
            Cancel
          </Button>
          <Button type="button" disabled={busy} onClick={onConfirm}>
            {busy ? "Continuing…" : "Continue"}
          </Button>
        </div>
      </Panel>
    </div>
  );
}
