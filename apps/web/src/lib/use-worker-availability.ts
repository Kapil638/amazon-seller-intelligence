"use client";

import { useEffect, useRef, useState } from "react";

import { fetchWorkerHealth } from "@/lib/api";

/**
 * fix/inventory-empty-response-and-failure-classification — the live
 * defect this closes: a Sync button could be clicked (and rejected with
 * a `worker_unavailable` trigger response) during the brief window
 * before a freshly-started worker publishes its first heartbeat, and
 * the resulting error banner never cleared itself once the worker
 * became healthy seconds later — nothing was polling `/health/workers`
 * from the page at all. This hook is the one, reusable fix: every
 * sync-domain page (Listings/Orders/Sales & Traffic/Inventory) uses it
 * identically rather than re-implementing its own polling.
 *
 * Deliberately a *separate* concept from a page's own sync-job-status
 * polling (e.g. `SellerInventory`'s own `loadSummary` polling loop) —
 * this only ever answers "is a worker of this type alive right now",
 * never anything about a specific run's progress or history. A
 * historical failed run must never be read as "the worker is
 * unhealthy" — this hook's own state is the only source of truth for
 * that, entirely independent of `summary.sync.status`.
 */
export type WorkerAvailabilityState = "unknown" | "starting" | "available" | "unavailable";

const DEFAULT_POLL_MS = 3000;
// Generous on purpose: a real worker's first heartbeat can be delayed
// by remote-database cold-start latency (observed directly this
// session — a worker's first heartbeat write can take up to ~60s
// against a high-latency remote Postgres under concurrent startup
// load), not just local process spin-up time.
const DEFAULT_GRACE_MS = 60000;

export function useWorkerAvailability(
  workerType: string,
  options?: { pollMs?: number; graceMs?: number },
): { state: WorkerAvailabilityState; lastHeartbeatAt: string | null } {
  const pollMs = options?.pollMs ?? DEFAULT_POLL_MS;
  const graceMs = options?.graceMs ?? DEFAULT_GRACE_MS;

  const [state, setState] = useState<WorkerAvailabilityState>("unknown");
  const [lastHeartbeatAt, setLastHeartbeatAt] = useState<string | null>(null);

  // Grace applies to *initial* startup only — once this worker type has
  // been observed available at least once this page-visit, a later gap
  // (a real crash/restart mid-session) is reported as unavailable
  // immediately, not re-shown as "starting" while a fresh grace window
  // silently masks it. Deliberately *not* initialized with `Date.now()`
  // here — reading the clock is an effect (runs after commit), never a
  // render-time computation (React's own purity rule for hooks/
  // components forbids calling an impure function like `Date.now()`
  // while rendering, even inside a `useRef` initializer, since that
  // initializer expression is still evaluated on every render).
  const startedAtRef = useRef<number | null>(null);
  const everAvailableRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    startedAtRef.current ??= Date.now();

    const tick = async () => {
      if (cancelled) return;
      const health = await fetchWorkerHealth();
      if (cancelled) return;

      const entry = health?.workers?.[workerType];
      if (entry?.available) {
        everAvailableRef.current = true;
        setState("available");
        setLastHeartbeatAt(entry.last_heartbeat_at);
      } else {
        setLastHeartbeatAt(entry?.last_heartbeat_at ?? null);
        if (everAvailableRef.current) {
          setState("unavailable");
        } else {
          const elapsed = Date.now() - (startedAtRef.current ?? Date.now());
          setState(elapsed >= graceMs ? "unavailable" : "starting");
        }
      }
      timer = setTimeout(tick, pollMs);
    };

    void tick();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [workerType, pollMs, graceMs]);

  return { state, lastHeartbeatAt };
}
