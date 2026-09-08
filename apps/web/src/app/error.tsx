"use client";

// fix/supervise-ingestion-runtime — a route-segment error boundary.
// Next.js's App Router renders nothing (a blank viewport) for an
// uncaught error thrown during render/effects unless a boundary like
// this one exists — exactly the failure mode a live inspection found:
// a reload produced a completely blank page. This catches any such
// error for every page under this segment and shows a clear, actionable
// message plus a retry action instead of a blank screen. Deliberately
// generic (this boundary has no way to know *why* something threw —
// it could be an API-unavailable condition already handled more
// specifically by individual pages via AmazonConnectionError/
// ListingsSyncError, or a genuine render bug) — see api-unavailable.tsx
// for the more specific "could not reach the server" state most seller
// pages already render on their own via their existing fetch error
// handling; this boundary is the last-resort catch-all beneath that.
import { useEffect } from "react";

export default function Error({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    // Never logs anything that could carry a credential/token/secret —
    // this is a client-side render error, not an API response body.
    console.error("[app] unhandled render error", error);
  }, [error]);

  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-background px-6 text-center text-foreground">
      <h1 className="text-lg font-semibold">Something went wrong</h1>
      <p className="max-w-md text-sm text-muted-foreground">
        This page hit an unexpected error. If the API server was just restarted, try again in a few
        seconds.
      </p>
      <button
        type="button"
        onClick={reset}
        className="rounded-md border border-input bg-surface px-4 py-2 text-sm font-medium hover:bg-surface-muted"
      >
        Try again
      </button>
    </div>
  );
}
