"use client";

import { useEffect, useState } from "react";

import { fetchAmazonConnection } from "@/lib/api";
import type { AmazonConnectionLifecycleStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

type HealthTone = "good" | "warning" | "bad" | "neutral";

const TONE_BY_STATUS: Record<AmazonConnectionLifecycleStatus, HealthTone> = {
  connected: "good",
  pending_validation: "warning",
  pending_authorization: "warning",
  degraded: "warning",
  not_connected: "neutral",
  revoked: "bad",
  error: "bad",
};

const DOT_CLASS: Record<HealthTone, string> = {
  good: "bg-emerald-500",
  warning: "bg-amber-500",
  bad: "bg-red-500",
  neutral: "bg-muted-foreground/40",
};

/**
 * Small, self-fetching Amazon connection health indicator for the
 * account/settings menu. Fails silently to the neutral tone on any error
 * (never blocks navigation rendering on a connection lookup) and never
 * displays a token, seller id, or other connection secret — only the
 * already-public `connection_status` enum.
 */
export function ConnectionHealthDot({ className }: { className?: string }) {
  const [tone, setTone] = useState<HealthTone>("neutral");

  useEffect(() => {
    let cancelled = false;
    Promise.resolve()
      .then(() => fetchAmazonConnection())
      .then((overview) => {
        if (!cancelled && overview) {
          setTone(TONE_BY_STATUS[overview.connection_status] ?? "neutral");
        }
      })
      .catch(() => {
        if (!cancelled) {
          setTone("neutral");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <span
      aria-hidden="true"
      className={cn("inline-block h-2 w-2 shrink-0 rounded-full", DOT_CLASS[tone], className)}
    />
  );
}
