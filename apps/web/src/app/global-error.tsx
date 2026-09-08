"use client";

// fix/supervise-ingestion-runtime — the root-layout error boundary.
// Reached only if the root layout itself (app/layout.tsx) throws —
// strictly narrower than error.tsx (which cannot catch an error in the
// layout that renders it). Must render its own complete <html>/<body>
// (Next.js replaces the entire document with this component in that
// case) and deliberately uses plain inline styles rather than Tailwind
// classes or any app component — this is the last line of defense
// against a genuinely blank page, so it must not depend on anything
// that could itself be part of what broke.
import { useEffect } from "react";

export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error("[app] unhandled root layout error", error);
  }, [error]);

  return (
    <html lang="en">
      <body
        style={{
          minHeight: "100vh",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: "1rem",
          padding: "1.5rem",
          textAlign: "center",
          fontFamily: "system-ui, sans-serif",
          background: "#0a0a0a",
          color: "#f5f5f5",
        }}
      >
        <h1 style={{ fontSize: "1.125rem", fontWeight: 600 }}>Something went wrong</h1>
        <p style={{ maxWidth: "28rem", fontSize: "0.875rem", color: "#a3a3a3" }}>
          The application failed to load. If the API server was just restarted, try again in a few
          seconds.
        </p>
        <button
          type="button"
          onClick={reset}
          style={{
            borderRadius: "0.375rem",
            border: "1px solid #404040",
            background: "#171717",
            color: "#f5f5f5",
            padding: "0.5rem 1rem",
            fontSize: "0.875rem",
            fontWeight: 500,
            cursor: "pointer",
          }}
        >
          Try again
        </button>
      </body>
    </html>
  );
}
