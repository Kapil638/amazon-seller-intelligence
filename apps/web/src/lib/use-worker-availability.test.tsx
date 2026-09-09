import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  fetchWorkerHealth: vi.fn(),
}));

import { fetchWorkerHealth } from "@/lib/api";
import { useWorkerAvailability } from "@/lib/use-worker-availability";

function healthOf(available: boolean, workerType = "inventory") {
  return { workers: { [workerType]: { available, last_heartbeat_at: available ? "2026-09-09T00:00:00.000Z" : null } } };
}

// The hook's first check runs synchronously inside its effect (never
// gated by the poll-interval timer) — flushing the microtask queue
// once is enough to observe its result. `waitFor`'s own default
// polling uses real timers, which never fire once `vi.useFakeTimers()`
// is active, so every test here flushes explicitly instead.
async function flush() {
  await act(async () => {
    await Promise.resolve();
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useWorkerAvailability — the single reusable implementation for all four sync domains", () => {
  it("starts unknown before the first health check resolves", () => {
    vi.mocked(fetchWorkerHealth).mockReturnValue(new Promise(() => {})); // never resolves
    const { result } = renderHook(() => useWorkerAvailability("inventory"));
    expect(result.current.state).toBe("unknown");
  });

  it("becomes available as soon as a fresh matching heartbeat appears", async () => {
    vi.mocked(fetchWorkerHealth).mockResolvedValue(healthOf(true));
    const { result } = renderHook(() => useWorkerAvailability("inventory"));
    await flush();
    expect(result.current.state).toBe("available");
    expect(result.current.lastHeartbeatAt).toBe("2026-09-09T00:00:00.000Z");
  });

  it("shows 'starting' (not 'unavailable') while still within the startup-grace window", async () => {
    vi.mocked(fetchWorkerHealth).mockResolvedValue(healthOf(false));
    const { result } = renderHook(() => useWorkerAvailability("inventory", { graceMs: 60000, pollMs: 1000 }));
    await flush();
    expect(result.current.state).toBe("starting");
  });

  it("automatically transitions from starting to available with no manual intervention", async () => {
    vi.mocked(fetchWorkerHealth).mockResolvedValue(healthOf(false));
    const { result } = renderHook(() => useWorkerAvailability("inventory", { graceMs: 60000, pollMs: 1000 }));
    await flush();
    expect(result.current.state).toBe("starting");

    vi.mocked(fetchWorkerHealth).mockResolvedValue(healthOf(true));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(result.current.state).toBe("available");
  });

  it("shows 'unavailable' once the startup-grace window expires with no heartbeat", async () => {
    vi.mocked(fetchWorkerHealth).mockResolvedValue(healthOf(false));
    const { result } = renderHook(() => useWorkerAvailability("inventory", { graceMs: 5000, pollMs: 1000 }));
    await flush();
    expect(result.current.state).toBe("starting");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000);
    });
    expect(result.current.state).toBe("unavailable");
  });

  it("a later gap after having been available goes straight to unavailable, never back to starting", async () => {
    vi.mocked(fetchWorkerHealth).mockResolvedValue(healthOf(true));
    const { result } = renderHook(() => useWorkerAvailability("inventory", { graceMs: 60000, pollMs: 1000 }));
    await flush();
    expect(result.current.state).toBe("available");

    // Worker crashes mid-session — a single missed heartbeat, well
    // inside what would have been the *startup* grace window.
    vi.mocked(fetchWorkerHealth).mockResolvedValue(healthOf(false));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(result.current.state).toBe("unavailable");
  });

  it("polls repeatedly at the configured interval", async () => {
    vi.mocked(fetchWorkerHealth).mockResolvedValue(healthOf(true));
    renderHook(() => useWorkerAvailability("inventory", { pollMs: 2000 }));
    await flush();
    expect(fetchWorkerHealth).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(fetchWorkerHealth).toHaveBeenCalledTimes(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(fetchWorkerHealth).toHaveBeenCalledTimes(3);
  });

  it("stops polling after unmount", async () => {
    vi.mocked(fetchWorkerHealth).mockResolvedValue(healthOf(true));
    const { unmount } = renderHook(() => useWorkerAvailability("inventory", { pollMs: 1000 }));
    await flush();
    expect(fetchWorkerHealth).toHaveBeenCalledTimes(1);

    unmount();
    const callsAtUnmount = vi.mocked(fetchWorkerHealth).mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(fetchWorkerHealth).toHaveBeenCalledTimes(callsAtUnmount);
  });

  it("a network failure (fetchWorkerHealth resolves null) is treated as not-available, never crashes", async () => {
    vi.mocked(fetchWorkerHealth).mockResolvedValue(null);
    const { result } = renderHook(() => useWorkerAvailability("inventory", { graceMs: 60000, pollMs: 1000 }));
    await flush();
    expect(result.current.state).toBe("starting");
    expect(result.current.lastHeartbeatAt).toBeNull();
  });

  it("only reports on its own worker_type — another type being available never leaks through", async () => {
    vi.mocked(fetchWorkerHealth).mockResolvedValue(healthOf(true, "orders"));
    const { result } = renderHook(() => useWorkerAvailability("inventory", { graceMs: 60000, pollMs: 1000 }));
    await flush();
    expect(result.current.state).toBe("starting");
  });
});
