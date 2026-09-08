#!/usr/bin/env bash
# fix/supervise-ingestion-runtime — thin entry point into the real
# process supervisor: scripts/supervisor.py.
#
# 12B.3H originally implemented this script's own startup/shutdown logic
# directly in bash — plain job control, `wait` on every child, no
# restart, no readiness, no heartbeat awareness. That was enough to
# start a stack, but not enough to notice or recover when a child died
# mid-session, which is exactly the gap fix/supervise-ingestion-runtime
# closes: a live inspection found the frontend and API both refusing
# connections with no supervisor anywhere to have noticed or restarted
# them. All of that logic now lives in scripts/supervisor.py (stdlib
# Python, no third-party dependency, unit-tested directly — see
# apps/api/tests/test_dev_supervisor.py) so it can be tested as real
# code rather than only as bash-script black-box behavior. This file
# stays as the documented, discoverable entry point.
#
# Supported platforms: macOS and Linux only (matches every other local
# tool in this repository). Requires: bash, python3 (3.12+, stdlib
# only — supervisor.py has no third-party dependency), uv, npm.
#
# Usage:
#   ./scripts/dev.sh                    # backend + frontend only (safe default)
#   ./scripts/dev.sh --with-workers     # also starts all three sync workers
#   ASI_LISTINGS_WORKER_ENABLED=true ./scripts/dev.sh   # start just one worker
#
# `--with-workers` is the one opt-in flag for connected-seller local
# development — it sets every ASI_*_WORKER_ENABLED flag internally so
# nobody has to remember or type three separate environment variables.
# It changes nothing else: the safe default (no flag, no env vars set)
# still starts zero workers, exactly as before — cloning this repository
# or copying `.env.example` and running `./scripts/dev.sh` still never
# starts a live worker. Equivalent to (and interchangeable with) setting
# each ASI_*_WORKER_ENABLED env var by hand; either path is fully
# supported.
#
# **This process must stay running in the foreground.** It is the
# supervisor — closing this terminal, or killing this process, stops
# the frontend, the API, and every worker it started, and stops
# synchronization from happening. Do not background it (`&`) or run it
# from a short-lived command — start it in a terminal you intend to
# keep open for the duration of your development session. Ctrl-C (SIGINT)
# or `kill <pid>` (SIGTERM) on this process stops everything it started,
# cleanly, and only then exits.
#
# Logs for every child are written under logs/dev/ (gitignored) and the
# exact path is printed on startup.

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "[dev.sh] ERROR: python3 is required (see Prerequisites in docs/AI_HANDOVER/14_LOCAL_DEVELOPMENT_SETUP.md)" >&2
  exit 1
fi

exec python3 "$ROOT_DIR/scripts/supervisor.py" "$@"
