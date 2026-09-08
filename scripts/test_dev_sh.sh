#!/usr/bin/env bash
# 12B.3H — Deterministic tests for scripts/dev.sh's process orchestration.
# Uses only fake `sleep`/`false` children via DEV_SH_*_CMD overrides — never
# the real backend, frontend, or Listings worker, and therefore never a
# live Amazon or Supabase connection. Run:
#
#   ./scripts/test_dev_sh.sh
#
# Exits 0 if every check passes, non-zero (with a message identifying
# which check failed) otherwise.
#
# Signals a running dev.sh with SIGTERM, not SIGINT, in every test here.
# `trap shutdown INT TERM` in dev.sh registers the identical handler for
# both, so this exercises the same code path Ctrl-C would — SIGTERM is
# used because it is what a real supervisor (and this test's own
# automation) sends, and because SIGINT specifically requires a
# controlling terminal to behave as expected when delivered to a
# background process, which a test runner does not provide.

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEV_SH="$ROOT_DIR/scripts/dev.sh"
FAILURES=0

pass() { echo "PASS: $1"; }
fail() {
  echo "FAIL: $1"
  FAILURES=$((FAILURES + 1))
}

wait_until() {
  # wait_until <seconds> <command...>
  local timeout="$1"
  shift
  local waited=0
  while ! "$@" >/dev/null 2>&1; do
    if [ "$waited" -ge "$timeout" ]; then
      return 1
    fi
    sleep 1
    waited=$((waited + 1))
  done
  return 0
}

no_sleep100_survivors() {
  ! pgrep -f "sleep 100" >/dev/null 2>&1
}

# --- 1: correct six child commands, all started ----------------------------

test_starts_six_children_and_shuts_down_cleanly() {
  local log
  log="$(mktemp)"
  ASI_LISTINGS_WORKER_ENABLED=true ASI_ORDERS_WORKER_ENABLED=true ASI_SALES_TRAFFIC_WORKER_ENABLED=true \
    ASI_INVENTORY_WORKER_ENABLED=true \
    BACKEND_PORT=18211 FRONTEND_PORT=18212 \
    DEV_SH_BACKEND_CMD="sleep 100" DEV_SH_FRONTEND_CMD="sleep 100" DEV_SH_WORKER_CMD="sleep 100" \
    DEV_SH_ORDERS_WORKER_CMD="sleep 100" DEV_SH_SALES_TRAFFIC_WORKER_CMD="sleep 100" \
    DEV_SH_INVENTORY_WORKER_CMD="sleep 100" \
    "$DEV_SH" >"$log" 2>&1 &
  local script_pid=$!

  if ! wait_until 5 grep -q "all requested processes are running" "$log"; then
    fail "startup: did not reach 'all requested processes are running' ($log)"
    kill -KILL "$script_pid" 2>/dev/null || true
    pkill -f "sleep 100" 2>/dev/null || true
    return
  fi
  local running
  running=$(pgrep -f "sleep 100" | wc -l | tr -d " ")
  if [ "$running" -ne 6 ]; then
    fail "startup: expected 6 'sleep 100' children, found $running"
  else
    pass "startup: exactly 6 child processes running (backend, frontend, listings/orders/sales-traffic/inventory workers)"
  fi

  kill -TERM "$script_pid" 2>/dev/null || true
  if wait_until 5 no_sleep100_survivors; then
    pass "shutdown: SIGTERM to dev.sh stopped every child, no orphans"
  else
    fail "shutdown: a 'sleep 100' child survived SIGTERM to dev.sh"
  fi
  if wait_until 5 bash -c "! kill -0 $script_pid 2>/dev/null"; then
    pass "shutdown: dev.sh's own process exited"
  else
    fail "shutdown: dev.sh's own process is still running"
  fi
  rm -f "$log"
  pkill -f "sleep 100" 2>/dev/null || true
}

# --- 1b: each worker flag is independent — enabling only one starts only it -

test_enabling_only_sales_traffic_worker_starts_only_that_one() {
  local log
  log="$(mktemp)"
  env -u ASI_LISTINGS_WORKER_ENABLED -u ASI_ORDERS_WORKER_ENABLED \
    ASI_SALES_TRAFFIC_WORKER_ENABLED=true \
    BACKEND_PORT=18225 FRONTEND_PORT=18226 \
    DEV_SH_BACKEND_CMD="sleep 100" DEV_SH_FRONTEND_CMD="sleep 100" \
    DEV_SH_SALES_TRAFFIC_WORKER_CMD="sleep 100" \
    "$DEV_SH" >"$log" 2>&1 &
  local script_pid=$!
  wait_until 5 grep -q "all requested processes are running" "$log" || true

  if grep -q "ASI_LISTINGS_WORKER_ENABLED is not set to true" "$log" && grep -q "ASI_ORDERS_WORKER_ENABLED is not set to true" "$log"; then
    pass "independent flags: Listings and Orders correctly withheld"
  else
    fail "independent flags: expected both Listings and Orders to be withheld ($log)"
  fi
  local running
  running=$(pgrep -f "sleep 100" | wc -l | tr -d " ")
  if [ "$running" -eq 3 ]; then
    pass "independent flags: exactly 3 children running (backend, frontend, sales-traffic worker only)"
  else
    fail "independent flags: expected exactly 3 children, found $running"
  fi

  kill -TERM "$script_pid" 2>/dev/null || true
  wait_until 5 no_sleep100_survivors || true
  rm -f "$log"
  pkill -f "sleep 100" 2>/dev/null || true
}

# --- 2: partial-start failure of a CRITICAL child cleans up everything ----
# (backend/frontend are the only critical children — see ChildSpec.critical
# in scripts/supervisor.py — so this uses DEV_SH_BACKEND_CMD="false" to
# simulate the backend itself failing to start.)

test_partial_start_failure_cleans_up() {
  local log
  log="$(mktemp)"
  ASI_LISTINGS_WORKER_ENABLED=true BACKEND_PORT=18213 FRONTEND_PORT=18214 \
    DEV_SH_BACKEND_CMD="false" DEV_SH_FRONTEND_CMD="sleep 100" DEV_SH_WORKER_CMD="sleep 100" \
    "$DEV_SH" >"$log" 2>&1
  local exit_code=$?

  if [ "$exit_code" -ne 0 ]; then
    pass "partial failure: dev.sh exited non-zero ($exit_code)"
  else
    fail "partial failure: dev.sh exited 0 despite a critical child failing to start"
  fi
  if grep -q "exited immediately during startup" "$log"; then
    pass "partial failure: reported which child failed to start"
  else
    fail "partial failure: no 'exited immediately during startup' message ($log)"
  fi
  if no_sleep100_survivors; then
    pass "partial failure: frontend/worker were torn down, no orphans"
  else
    fail "partial failure: a 'sleep 100' child survived the failed startup"
  fi
  rm -f "$log"
  pkill -f "sleep 100" 2>/dev/null || true
}

# --- 2b: a NON-critical worker failing at startup never takes the stack ----
# down — an absent/broken worker must be a safe, well-defined state (Sync
# returns a structured 503; frontend/API and any other worker keep running).

test_non_critical_worker_startup_failure_does_not_stop_the_stack() {
  local log
  log="$(mktemp)"
  ASI_LISTINGS_WORKER_ENABLED=true BACKEND_PORT=18229 FRONTEND_PORT=18230 \
    DEV_SH_READINESS_TIMEOUT_SECONDS="${DEV_SH_READINESS_TIMEOUT_SECONDS:-2}" \
    DEV_SH_BACKEND_CMD="sleep 100" DEV_SH_FRONTEND_CMD="sleep 100" DEV_SH_WORKER_CMD="false" \
    "$DEV_SH" >"$log" 2>&1 &
  local script_pid=$!

  if wait_until 10 grep -q "all requested processes are running" "$log"; then
    pass "non-critical worker failure: backend + frontend still reached the running state"
  else
    fail "non-critical worker failure: stack never reached the running state ($log)"
  fi
  if grep -q "exited immediately during startup" "$log"; then
    pass "non-critical worker failure: reported which worker failed to start"
  else
    fail "non-critical worker failure: no 'exited immediately during startup' message ($log)"
  fi
  local running
  running=$(pgrep -f "sleep 100" | wc -l | tr -d " ")
  if [ "$running" -eq 2 ]; then
    pass "non-critical worker failure: backend and frontend both still running"
  else
    fail "non-critical worker failure: expected exactly 2 surviving 'sleep 100' children, found $running"
  fi

  kill -TERM "$script_pid" 2>/dev/null || true
  wait_until 5 no_sleep100_survivors || true
  rm -f "$log"
  pkill -f "sleep 100" 2>/dev/null || true
}

# --- 3: port conflict detected before starting anything --------------------

test_port_conflict_is_detected() {
  local blocker_log
  blocker_log="$(mktemp)"
  ( exec 18215<>/dev/null; python3 -c "
import socket, time
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('127.0.0.1', 18215))
s.listen(1)
time.sleep(10)
" ) >"$blocker_log" 2>&1 &
  local blocker_pid=$!
  sleep 1

  local log
  log="$(mktemp)"
  ASI_LISTINGS_WORKER_ENABLED=true BACKEND_PORT=18215 FRONTEND_PORT=18216 \
    DEV_SH_BACKEND_CMD="sleep 100" DEV_SH_FRONTEND_CMD="sleep 100" DEV_SH_WORKER_CMD="sleep 100" \
    "$DEV_SH" >"$log" 2>&1
  local exit_code=$?

  if [ "$exit_code" -ne 0 ] && grep -q "already in use" "$log"; then
    pass "port conflict: dev.sh refused to start and reported the conflict"
  else
    fail "port conflict: dev.sh did not cleanly detect the busy port ($log)"
  fi
  kill -TERM "$blocker_pid" 2>/dev/null || true
  rm -f "$log" "$blocker_log"
  pkill -f "sleep 100" 2>/dev/null || true
}

# --- 4: duplicate invocation does not start a second worker ---------------

test_does_not_start_a_duplicate_worker() {
  local fake_worker_script
  fake_worker_script="$(mktemp)"
  cat >"$fake_worker_script" <<'FAKE'
#!/usr/bin/env bash
sleep 100
FAKE
  chmod +x "$fake_worker_script"
  # The literal string "app.amazon.listings_worker" must appear in the
  # command line for dev.sh's `pgrep -f` check to recognize it, exactly
  # as it would for the real `uv run python -m app.amazon.listings_worker`.
  local marked_script="${fake_worker_script}.app.amazon.listings_worker.sh"
  mv "$fake_worker_script" "$marked_script"
  "$marked_script" &
  local fake_worker_pid=$!
  sleep 1

  local log
  log="$(mktemp)"
  ASI_LISTINGS_WORKER_ENABLED=true BACKEND_PORT=18217 FRONTEND_PORT=18218 \
    DEV_SH_BACKEND_CMD="sleep 100" DEV_SH_FRONTEND_CMD="sleep 100" DEV_SH_WORKER_CMD="sleep 100" \
    "$DEV_SH" >"$log" 2>&1 &
  local script_pid=$!

  if wait_until 5 grep -q "already appears to be running" "$log"; then
    pass "duplicate worker: detected the pre-existing worker and skipped starting a second one"
  else
    fail "duplicate worker: no detection message found ($log)"
  fi
  wait_until 5 grep -q "started frontend" "$log" || true
  local worker_children
  worker_children=$(pgrep -f "sleep 100" | wc -l | tr -d " ")
  # Exactly 2 dev.sh-started children (backend, frontend) plus the one
  # pre-existing fake worker = 3 "sleep 100" processes total, never 4.
  if [ "$worker_children" -eq 3 ]; then
    pass "duplicate worker: exactly one worker-shaped process exists (no second one started)"
  else
    fail "duplicate worker: expected 3 total 'sleep 100' processes (pre-existing + backend + frontend), found $worker_children"
  fi

  kill -TERM "$script_pid" 2>/dev/null || true
  wait_until 5 bash -c "! kill -0 $script_pid 2>/dev/null" || true
  kill -KILL "$fake_worker_pid" 2>/dev/null || true
  rm -f "$log" "$marked_script"
  pkill -f "sleep 100" 2>/dev/null || true
}

# --- 5: command construction never exposes secrets -------------------------

test_command_construction_never_prints_secrets() {
  # dev.sh never reads .env / credentials itself — it only launches the
  # existing, already-reviewed uv/npm/worker commands, which are
  # themselves responsible for their own secret handling. This asserts
  # the negative directly against the script's own source: no credential-
  # shaped literal ever appears in it.
  if grep -qiE "SP_API_LWA_CLIENT_SECRET|password|DATABASE_URL=postgres" "$DEV_SH"; then
    fail "command construction: dev.sh's own source contains a credential-shaped literal"
  else
    pass "command construction: no credential-shaped literal in dev.sh"
  fi
}

# --- 5b: worker authorization gate — disabled by default -------------------

test_worker_not_started_without_the_enable_flag() {
  local log
  log="$(mktemp)"
  # Deliberately none of the four ASI_*_WORKER_ENABLED flags at all —
  # the default, fail-closed state this whole gate exists to prove, for
  # all four workers independently.
  env -u ASI_LISTINGS_WORKER_ENABLED -u ASI_ORDERS_WORKER_ENABLED -u ASI_SALES_TRAFFIC_WORKER_ENABLED \
    -u ASI_INVENTORY_WORKER_ENABLED \
    BACKEND_PORT=18223 FRONTEND_PORT=18224 \
    DEV_SH_BACKEND_CMD="sleep 100" DEV_SH_FRONTEND_CMD="sleep 100" DEV_SH_WORKER_CMD="sleep 100" \
    DEV_SH_ORDERS_WORKER_CMD="sleep 100" DEV_SH_SALES_TRAFFIC_WORKER_CMD="sleep 100" \
    DEV_SH_INVENTORY_WORKER_CMD="sleep 100" \
    "$DEV_SH" >"$log" 2>&1 &
  local script_pid=$!
  wait_until 5 grep -q "all requested processes are running" "$log" || true

  if grep -q "ASI_LISTINGS_WORKER_ENABLED is not set to true" "$log" \
    && grep -q "ASI_ORDERS_WORKER_ENABLED is not set to true" "$log" \
    && grep -q "ASI_SALES_TRAFFIC_WORKER_ENABLED is not set to true" "$log" \
    && grep -q "ASI_INVENTORY_WORKER_ENABLED is not set to true" "$log"; then
    pass "worker gate: dev.sh explained why it is not starting any of the four workers"
  else
    fail "worker gate: missing explanation for at least one disabled worker ($log)"
  fi
  local running
  running=$(pgrep -f "sleep 100" | wc -l | tr -d " ")
  if [ "$running" -eq 2 ]; then
    pass "worker gate: only backend and frontend started (all four workers withheld by default)"
  else
    fail "worker gate: expected exactly 2 children (backend, frontend) without any flag, found $running"
  fi

  kill -TERM "$script_pid" 2>/dev/null || true
  wait_until 5 no_sleep100_survivors || true
  rm -f "$log"
  pkill -f "sleep 100" 2>/dev/null || true
}

# --- 6: shutdown never touches a process this invocation did not start ----

test_shutdown_never_touches_an_unrelated_process() {
  # A distinct sleep duration (200, not 100) so this process can never be
  # confused with the "sleep 100" fakes dev.sh itself starts elsewhere in
  # this suite — proving dev.sh's shutdown only ever signals PIDs it
  # itself recorded (see start_child/shutdown in dev.sh: every kill uses
  # an explicit tracked $pid, never a broad pkill/pattern match), not
  # "every sleep-like process on the system."
  sleep 200 &
  local unrelated_pid=$!

  local log
  log="$(mktemp)"
  ASI_LISTINGS_WORKER_ENABLED=true BACKEND_PORT=18219 FRONTEND_PORT=18220 \
    DEV_SH_BACKEND_CMD="sleep 100" DEV_SH_FRONTEND_CMD="sleep 100" DEV_SH_WORKER_CMD="sleep 100" \
    "$DEV_SH" >"$log" 2>&1 &
  local script_pid=$!
  wait_until 5 grep -q "all requested processes are running" "$log" || true

  kill -TERM "$script_pid" 2>/dev/null || true
  wait_until 5 no_sleep100_survivors || true

  if kill -0 "$unrelated_pid" 2>/dev/null; then
    pass "shutdown: an unrelated process never started by dev.sh survived untouched"
  else
    fail "shutdown: an unrelated process was killed — dev.sh signaled something it never started"
  fi

  kill -KILL "$unrelated_pid" 2>/dev/null || true
  rm -f "$log"
  pkill -f "sleep 100" 2>/dev/null || true
}

# --- 7: no secret material in dev.sh's own runtime output ------------------

test_runtime_output_never_contains_a_database_url_or_token() {
  local log
  log="$(mktemp)"
  ASI_LISTINGS_WORKER_ENABLED=true BACKEND_PORT=18221 FRONTEND_PORT=18222 \
    DEV_SH_BACKEND_CMD="sleep 100" DEV_SH_FRONTEND_CMD="sleep 100" DEV_SH_WORKER_CMD="sleep 100" \
    "$DEV_SH" >"$log" 2>&1 &
  local script_pid=$!
  wait_until 5 grep -q "all requested processes are running" "$log" || true
  kill -TERM "$script_pid" 2>/dev/null || true
  wait_until 5 no_sleep100_survivors || true

  if grep -qiE "DATABASE_URL=|postgres(ql)?://[^ ]*:.*@|Bearer [A-Za-z0-9._-]+" "$log"; then
    fail "runtime output: dev.sh's own log contains a credential-shaped value ($log)"
  else
    pass "runtime output: no credential-shaped value in dev.sh's own log"
  fi
  rm -f "$log"
  pkill -f "sleep 100" 2>/dev/null || true
}

# --- 8: --with-workers is one opt-in flag for the connected-seller mode ---

test_with_workers_flag_starts_all_four_workers_without_env_vars() {
  local log
  log="$(mktemp)"
  # Deliberately none of the four ASI_*_WORKER_ENABLED flags set by
  # hand — --with-workers alone must be sufficient (fix/ingestion-
  # worker-runtime-availability's own opt-in mode, extended to Inventory
  # once PR #22 rebased onto it).
  env -u ASI_LISTINGS_WORKER_ENABLED -u ASI_ORDERS_WORKER_ENABLED -u ASI_SALES_TRAFFIC_WORKER_ENABLED \
    -u ASI_INVENTORY_WORKER_ENABLED \
    BACKEND_PORT=18227 FRONTEND_PORT=18228 \
    DEV_SH_BACKEND_CMD="sleep 100" DEV_SH_FRONTEND_CMD="sleep 100" DEV_SH_WORKER_CMD="sleep 100" \
    DEV_SH_ORDERS_WORKER_CMD="sleep 100" DEV_SH_SALES_TRAFFIC_WORKER_CMD="sleep 100" \
    DEV_SH_INVENTORY_WORKER_CMD="sleep 100" \
    "$DEV_SH" --with-workers >"$log" 2>&1 &
  local script_pid=$!

  if ! wait_until 5 grep -q "all requested processes are running" "$log"; then
    fail "--with-workers: did not reach 'all requested processes are running' ($log)"
    kill -KILL "$script_pid" 2>/dev/null || true
    pkill -f "sleep 100" 2>/dev/null || true
    return
  fi
  local running
  running=$(pgrep -f "sleep 100" | wc -l | tr -d " ")
  if [ "$running" -eq 6 ]; then
    pass "--with-workers: exactly 6 children running with no env vars set by hand"
  else
    fail "--with-workers: expected exactly 6 children, found $running"
  fi

  kill -TERM "$script_pid" 2>/dev/null || true
  wait_until 5 no_sleep100_survivors || true
  rm -f "$log"
  pkill -f "sleep 100" 2>/dev/null || true
}

# --- 9b: a second concurrent invocation is refused via the PID file -------
# (fix/supervise-ingestion-runtime: scripts/supervisor.py's own PID file,
# not a port check — different ports are used here specifically so the
# only thing that can refuse the second invocation is the PID file.)

test_second_concurrent_invocation_is_refused_by_the_pid_file() {
  local log1 log2
  log1="$(mktemp)"
  log2="$(mktemp)"
  ASI_LISTINGS_WORKER_ENABLED=true BACKEND_PORT=18231 FRONTEND_PORT=18232 \
    DEV_SH_BACKEND_CMD="sleep 100" DEV_SH_FRONTEND_CMD="sleep 100" DEV_SH_WORKER_CMD="sleep 100" \
    "$DEV_SH" >"$log1" 2>&1 &
  local first_pid=$!
  if ! wait_until 5 grep -q "all requested processes are running" "$log1"; then
    fail "pid file: first supervisor never reached the running state ($log1)"
    kill -KILL "$first_pid" 2>/dev/null || true
    pkill -f "sleep 100" 2>/dev/null || true
    rm -f "$log1" "$log2"
    return
  fi

  BACKEND_PORT=18233 FRONTEND_PORT=18234 "$DEV_SH" >"$log2" 2>&1
  local second_exit=$?

  if [ "$second_exit" -ne 0 ] && grep -qi "already" "$log2"; then
    pass "pid file: a second concurrent invocation was refused, citing the live supervisor"
  else
    fail "pid file: second invocation was not cleanly refused (exit=$second_exit, $log2)"
  fi
  # The second, refused invocation must never have started its own
  # children on its own (different) ports.
  if pgrep -f "sleep 100" | wc -l | tr -d " " | grep -q "^3$"; then
    pass "pid file: the refused second invocation started no children of its own"
  else
    fail "pid file: unexpected child-process count after the refused second invocation"
  fi

  kill -TERM "$first_pid" 2>/dev/null || true
  wait_until 5 no_sleep100_survivors || true
  rm -f "$log1" "$log2"
  pkill -f "sleep 100" 2>/dev/null || true
}

# --- 9: an unrecognized flag is rejected, not silently ignored -------------

test_unrecognized_flag_is_rejected() {
  local log
  log="$(mktemp)"
  "$DEV_SH" --not-a-real-flag >"$log" 2>&1
  local exit_code=$?
  if [ "$exit_code" -ne 0 ] && grep -q "Unrecognized argument" "$log"; then
    pass "unrecognized flag: rejected with a clear message and nonzero exit, nothing started"
  else
    fail "unrecognized flag: expected a nonzero exit and a clear message ($log)"
  fi
  rm -f "$log"
}

test_starts_six_children_and_shuts_down_cleanly
test_enabling_only_sales_traffic_worker_starts_only_that_one
test_partial_start_failure_cleans_up
test_non_critical_worker_startup_failure_does_not_stop_the_stack
test_port_conflict_is_detected
test_does_not_start_a_duplicate_worker
test_worker_not_started_without_the_enable_flag
test_shutdown_never_touches_an_unrelated_process
test_runtime_output_never_contains_a_database_url_or_token
test_command_construction_never_prints_secrets
test_with_workers_flag_starts_all_four_workers_without_env_vars
test_second_concurrent_invocation_is_refused_by_the_pid_file
test_unrecognized_flag_is_rejected

echo ""
if [ "$FAILURES" -eq 0 ]; then
  echo "All scripts/dev.sh orchestration checks passed."
  exit 0
else
  echo "$FAILURES scripts/dev.sh orchestration check(s) failed."
  exit 1
fi
