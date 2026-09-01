#!/usr/bin/env bash
# 12B.3H — Unified local development startup: frontend + backend API +
# exactly one Listings worker, in one terminal, one Ctrl-C.
#
# Supported platforms: macOS and Linux only (this repo's other local
# tooling — e.g. the disposable-Postgres backup scripts under
# docs/AI_HANDOVER — already assumes the same; Windows is not a
# supported local development target). Requires: bash, lsof, pgrep,
# uv, npm — all already required by this project's existing individual
# start commands.
#
# This does not replace the existing individual commands (still
# documented in docs/AI_HANDOVER/14_LOCAL_DEVELOPMENT_SETUP.md) — it
# only saves opening three terminals for the common case of wanting all
# three running together. No process manager dependency is added; this
# is plain bash job control.
#
# Usage:
#   ./scripts/dev.sh
#
# Ctrl-C (SIGINT) or `kill <pid>` (SIGTERM) on this script's own process
# stops all three children.

set -uo pipefail
set -m # job control: each backgrounded child becomes its own process group leader

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_DIR="$ROOT_DIR/apps/api"
WEB_DIR="$ROOT_DIR/apps/web"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"

# PIDs of children this invocation actually started — only these are
# ever signaled or waited on. A worker this script chose not to start
# (see the duplicate-worker check below) is never added here.
declare -a CHILD_PIDS=()
declare -a CHILD_NAMES=()
SHUTTING_DOWN=0

log() {
  printf '[dev.sh] %s\n' "$1"
}

port_in_use() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN -t >/dev/null 2>&1
}

# Prefixes a child's combined stdout/stderr with a short tag, without
# ever touching the content itself — never a place secrets could be
# redacted-and-missed, since nothing here parses or rewrites log lines.
prefixed() {
  local tag="$1"
  sed -u "s/^/[$tag] /"
}

start_child() {
  local name="$1" dir="$2"
  shift 2
  # Process substitution, not a `| prefixed` pipe: backgrounding a
  # pipeline's `$!` gives the PID of its *last* stage (the `sed`
  # prefixer here), not the actual command — `shutdown` would then be
  # signaling the log formatter while the real backend/frontend/worker
  # process it was piped into keeps running, undetected, as an orphan.
  # `> >(...)` keeps the backgrounded command's own PID in `$!`.
  (
    cd "$dir" || exit 1
    exec "$@"
  ) > >(prefixed "$name") 2>&1 &
  local pid=$!
  CHILD_PIDS+=("$pid")
  CHILD_NAMES+=("$name")
  log "started $name (pid $pid)"
}

child_alive() {
  kill -0 "$1" 2>/dev/null
}

shutdown() {
  if [ "$SHUTTING_DOWN" -eq 1 ]; then
    return
  fi
  SHUTTING_DOWN=1
  # Guard every array expansion on the count first — under bash 3.2's
  # `set -u` (macOS's default, non-interactive /bin/bash), expanding
  # `${!ARRAY[@]}` or `${ARRAY[@]}` for a still-empty array raises
  # "unbound variable" instead of iterating zero times like newer bash.
  # Reached whenever shutdown fires before any child ever started (e.g.
  # the port pre-flight check failed) — must be a clean no-op, not a
  # second error on top of the real one.
  if [ "${#CHILD_PIDS[@]}" -eq 0 ]; then
    return
  fi
  log "shutting down..."
  local i pid name
  for i in "${!CHILD_PIDS[@]}"; do
    pid="${CHILD_PIDS[$i]}"
    name="${CHILD_NAMES[$i]}"
    if child_alive "$pid"; then
      log "stopping $name (pid $pid)"
      # Negative PID = the whole process group `set -m` gave this child,
      # so uv/npm/next's own subprocesses are signaled too, not just the
      # immediate wrapper — this is what actually avoids an orphaned
      # next-server or uvicorn reloader child surviving this script.
      kill -TERM "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    fi
  done
  # Grace period for cooperative shutdown (matches the worker's own
  # cooperative-stop design — see app/amazon/listings_worker.py) before
  # escalating to SIGKILL for anything still alive.
  local waited=0 any_alive
  while [ "$waited" -lt 10 ]; do
    any_alive=0
    for i in "${!CHILD_PIDS[@]}"; do
      if child_alive "${CHILD_PIDS[$i]}"; then
        any_alive=1
      fi
    done
    if [ "$any_alive" -eq 0 ]; then
      break
    fi
    sleep 1
    waited=$((waited + 1))
  done
  for i in "${!CHILD_PIDS[@]}"; do
    pid="${CHILD_PIDS[$i]}"
    if child_alive "$pid"; then
      log "force-stopping ${CHILD_NAMES[$i]} (pid $pid)"
      kill -KILL "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
    fi
  done
  log "all children stopped"
}

trap shutdown INT TERM
trap shutdown EXIT

# --- pre-flight: port conflicts --------------------------------------------

if port_in_use "$BACKEND_PORT"; then
  log "ERROR: port $BACKEND_PORT is already in use — is the backend already running (this script or another terminal)?"
  exit 1
fi
if port_in_use "$FRONTEND_PORT"; then
  log "ERROR: port $FRONTEND_PORT is already in use — is the frontend already running (this script or another terminal)?"
  exit 1
fi

# --- pre-flight: worker authorization gate ----------------------------------
# 12B.3H — starting this unified stack must never *silently* begin
# claiming and processing real jobs (real Amazon calls, against whatever
# DATABASE_URL is configured — a live Supabase project in this repo's
# actual local .env, not a disposable one) just because a developer ran
# this script without thinking about it. The worker module enforces this
# same gate itself (fail-closed, checked first thing in its own `main()`)
# — checking it here too means a disabled worker is never even attempted,
# rather than started and immediately exiting with a visible error.

SKIP_WORKER=0
if [ "${ASI_LISTINGS_WORKER_ENABLED:-}" != "1" ] && [ "$(printf '%s' "${ASI_LISTINGS_WORKER_ENABLED:-}" | tr '[:upper:]' '[:lower:]')" != "true" ]; then
  log "ASI_LISTINGS_WORKER_ENABLED is not set to true — not starting a Listings worker."
  log "(backend and frontend still start normally; set ASI_LISTINGS_WORKER_ENABLED=true to also start the worker — see docs/AI_HANDOVER/12B3H_LISTINGS_WORKER_OPERATIONS.md)"
  SKIP_WORKER=1
fi

# --- pre-flight: duplicate worker -------------------------------------------

if [ "$SKIP_WORKER" -eq 0 ] && pgrep -f "app\.amazon\.listings_worker" >/dev/null 2>&1; then
  log "a Listings worker process already appears to be running — not starting a second one."
  log "(this script never claims two workers are safe to run without checking; see app/amazon/listings_worker.py's own module docstring for why a duplicate worker is otherwise harmless, just wasteful — this check exists purely to avoid confusing duplicate log output)"
  SKIP_WORKER=1
fi

# --- start ------------------------------------------------------------------
# Each command can be overridden via a single environment variable purely
# so an automated test can substitute a safe, deterministic fake process
# (e.g. `sleep 100`) in place of the real backend/frontend/worker — never
# intended for interactive/production use, and a normal invocation of
# this script (none of these set) runs exactly the real commands below.
# Word-split deliberately (an override is a whole command + its
# arguments, e.g. "sleep 100"); never used with untrusted input.

if [ -n "${DEV_SH_BACKEND_CMD:-}" ]; then
  # shellcheck disable=SC2206
  BACKEND_CMD=($DEV_SH_BACKEND_CMD)
else
  BACKEND_CMD=(uv run uvicorn app.main:app --reload --port "$BACKEND_PORT")
fi

if [ -n "${DEV_SH_FRONTEND_CMD:-}" ]; then
  # shellcheck disable=SC2206
  FRONTEND_CMD=($DEV_SH_FRONTEND_CMD)
else
  FRONTEND_CMD=(npm run dev)
fi

if [ -n "${DEV_SH_WORKER_CMD:-}" ]; then
  # shellcheck disable=SC2206
  WORKER_CMD=($DEV_SH_WORKER_CMD)
else
  WORKER_CMD=(uv run python -m app.amazon.listings_worker)
fi

start_child "backend" "$API_DIR" "${BACKEND_CMD[@]}"
start_child "frontend" "$WEB_DIR" "${FRONTEND_CMD[@]}"

if [ "$SKIP_WORKER" -eq 0 ]; then
  start_child "worker" "$API_DIR" "${WORKER_CMD[@]}"
fi

# --- partial-start-failure check --------------------------------------------
# A brief grace period, then confirm everything actually stayed up before
# settling in to `wait` — a child that crashes immediately (bad config,
# missing dependency, etc.) must tear the others down, not leave them
# running alone.

sleep 2
if [ "$SHUTTING_DOWN" -eq 1 ]; then
  # A shutdown signal arrived during this grace period — `shutdown` has
  # already stopped everything intentionally; every child now being dead
  # is the expected result of that, never a startup failure to report.
  exit 0
fi
FAILED=0
for i in "${!CHILD_PIDS[@]}"; do
  if ! child_alive "${CHILD_PIDS[$i]}"; then
    log "ERROR: ${CHILD_NAMES[$i]} exited immediately during startup"
    FAILED=1
  fi
done
if [ "$FAILED" -eq 1 ]; then
  exit 1
fi

log "all requested processes are running. Press Ctrl-C to stop everything."

# Deliberately `wait`, not a `while ...; sleep 1; done` polling loop: a
# trapped signal only reliably interrupts a shell that is blocked in
# `wait` — verified directly, in this exact environment, against a
# `sleep`-in-a-loop polling pattern, which left a pending SIGINT/SIGTERM
# undelivered to the trap for as long as the loop kept running. `wait -n`
# (bash 4.3+, "return as soon as any one of these exits") is not used
# either — macOS ships bash 3.2 by default, and this script must work
# there without requiring a newer bash to be installed. Waiting on every
# child means a Ctrl-C/SIGTERM is always caught immediately (the `wait`
# is interrupted the instant the signal arrives, regardless of which
# child it was "waiting" on) — the one tradeoff is that if a single
# child crashes on its own *mid-session* (not during the startup grace
# period already checked above) while the others keep running, this
# script will not proactively notice until every child has exited or a
# shutdown signal arrives; it never fails to react to an actual Ctrl-C.
wait "${CHILD_PIDS[@]}" 2>/dev/null || true
log "child processes exited"
