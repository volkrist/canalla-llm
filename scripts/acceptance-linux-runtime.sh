#!/usr/bin/env bash
# Linux runtime acceptance: the sidecar Canalla ships plus the Tor daemon Canalla ships.
#
# This is the half of the Linux product that needs no desktop session, so it can be certified
# anywhere a Linux box is: the packaged backend must serve itself with no interpreter on PATH, the
# bundled Tor runtime must boot, prove a real circuit, and come back by itself when it is killed.
#
# Usage:
#   scripts/acceptance-linux-runtime.sh [--sidecar PATH] [--tor-runtime DIR] [--data DIR]
#
# Every wait is bounded. Nothing is installed, nothing is downloaded, no GPU and no network
# provider is involved: the only network use is Tor's own bootstrap.

set -u

SIDECAR="apps/backend/dist/alex-backend/alex-backend"
TOR_RUNTIME="apps/desktop/src-tauri/runtime/tor"
DATA="/tmp/canalla-linux-acceptance"
HEALTH_TIMEOUT=90
TOR_TIMEOUT=300
RECOVERY_TIMEOUT=240

PLATFORM="linux-x86_64"
PIN_FILE="scripts/tor-runtime.json"

while [ $# -gt 0 ]; do
  case "$1" in
    --sidecar) SIDECAR="$2"; shift 2 ;;
    --tor-runtime) TOR_RUNTIME="$2"; shift 2 ;;
    --data) DATA="$2"; shift 2 ;;
    --pin) PIN_FILE="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

# Absolute paths everywhere: a relative one changes meaning under a spawned child, and the
# daemon's library path is built from this directory.
SIDECAR=$(realpath "$SIDECAR" 2>/dev/null || echo "$SIDECAR")
TOR_RUNTIME=$(realpath "$TOR_RUNTIME" 2>/dev/null || echo "$TOR_RUNTIME")
DATA=$(realpath -m "$DATA" 2>/dev/null || echo "$DATA")
PINNED=$(sed -n "s/.*\"daemon_version\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" "$PIN_FILE" 2>/dev/null | head -1)

failures=0
check() { # check NAME OK DETAIL
  if [ "$2" = "1" ]; then
    echo "PASS $1${3:+  [$3]}"
  else
    echo "FAIL $1${3:+  [$3]}"
    failures=$((failures + 1))
  fi
}

value() { # value FILE KEY -> the first string/number/bool after "key":
  sed -n "s/.*\"$2\"[[:space:]]*:[[:space:]]*\([^,}]*\).*/\1/p" "$1" 2>/dev/null | tr -d '"' | head -1
}

if [ ! -x "$SIDECAR" ]; then
  echo "no sidecar at $SIDECAR (build it with: pyinstaller alex-backend.spec)" >&2
  exit 2
fi
if [ ! -d "$TOR_RUNTIME" ]; then
  echo "no bundled Tor runtime at $TOR_RUNTIME (stage it with scripts/fetch-tor-runtime.py)" >&2
  exit 2
fi

echo "LINUX RUNTIME ACCEPTANCE — packaged backend + bundled Tor, no interpreter on PATH"
echo "  sidecar  $SIDECAR"
echo "  tor      $TOR_RUNTIME"
echo "  data     $DATA"
echo "  pinned   ${PINNED:-unknown}"
echo

rm -rf "$DATA"
mkdir -p "$DATA"
EMPTY_PATH="$DATA/empty-path"
mkdir -p "$EMPTY_PATH"

# The packaged backend is started with a PATH that holds nothing at all: if it needed a system
# interpreter or a shell tool, it could not answer.
env -i HOME="$DATA/home" PATH="$EMPTY_PATH" ALEX_LLM_DATA_DIR="$DATA" \
  ALEX_TOR_RUNTIME_DIR="$TOR_RUNTIME" \
  "$SIDECAR" >"$DATA/sidecar.log" 2>&1 &
SIDE_PID=$!
echo "  sidecar pid $SIDE_PID"

cleanup() {
  kill "$SIDE_PID" 2>/dev/null
  sleep 3
  kill -9 "$SIDE_PID" 2>/dev/null
}
trap cleanup EXIT

# --------------------------------------------------------------------------------- backend up
deadline=$((SECONDS + HEALTH_TIMEOUT))
health=""
while [ $SECONDS -lt $deadline ]; do
  health=$(curl -fsS http://127.0.0.1:8000/health 2>/dev/null) && break
  sleep 2
done
echo "$health" >"$DATA/health.json"
check "the packaged backend answers /health with no PATH at all" \
  "$([ -n "$health" ] && echo 1 || echo 0)" "$(value "$DATA/health.json" product)"
check "it reports the product, not a stand-in" \
  "$([ "$(value "$DATA/health.json" product)" = "alex-llm" ] && echo 1 || echo 0)" \
  "product=$(value "$DATA/health.json" product)"

# -------------------------------------------------------------------- bundled Tor, real proof
proof="$DATA/runtime/tor.json"
deadline=$((SECONDS + TOR_TIMEOUT))
verified=""
while [ $SECONDS -lt $deadline ]; do
  if [ -f "$proof" ] && [ "$(value "$proof" verified)" = "true" ]; then
    verified="1"
    break
  fi
  sleep 3
done
check "the bundled Tor daemon bootstraps and proves a circuit" \
  "$([ -n "$verified" ] && echo 1 || echo 0)" \
  "state=$(value "$proof" source) endpoint=$(value "$proof" port)"
check "the route is proven through SOCKS, never a clearnet fallback" \
  "$([ "$(value "$proof" method)" = "socks5h" ] && echo 1 || echo 0)" \
  "method=$(value "$proof" method)"
check "the daemon that proved it is the pinned build Canalla ships" \
  "$([ -n "$PINNED" ] && [ "$(value "$proof" tor_version)" = "$PINNED" ] && echo 1 || echo 0)" \
  "version=$(value "$proof" tor_version) (pin $PINNED)"

first_pid=$(value "$proof" pid)
serving=$(readlink "/proc/$first_pid/exe" 2>/dev/null)
check "the proof names the process that serves it, and it is the shipped binary" \
  "$([ "$serving" = "$TOR_RUNTIME/tor" ] && echo 1 || echo 0)" "pid=$first_pid exe=$serving"

# ------------------------------------------------------------------------------- kill and wait
if [ -n "$first_pid" ] && [ "$first_pid" != "null" ]; then
  kill "$first_pid" 2>/dev/null
fi
deadline=$((SECONDS + RECOVERY_TIMEOUT))
second_pid=""
while [ $SECONDS -lt $deadline ]; do
  now=$(value "$proof" pid)
  if [ "$(value "$proof" verified)" = "true" ] && [ -n "$now" ] && [ "$now" != "$first_pid" ]; then
    second_pid="$now"
    break
  fi
  sleep 3
done
check "killing the daemon is recovered by a new process, with a fresh proof" \
  "$([ -n "$second_pid" ] && echo 1 || echo 0)" "$first_pid -> $second_pid"

health=$(curl -fsS http://127.0.0.1:8000/health 2>/dev/null)
check "the backend keeps serving through the daemon's restart" \
  "$([ -n "$health" ] && echo 1 || echo 0)"

# ------------------------------------------------------------------------------- clean shutdown
kill "$SIDE_PID" 2>/dev/null
deadline=$((SECONDS + 60))
while [ $SECONDS -lt $deadline ]; do
  if ! kill -0 "$SIDE_PID" 2>/dev/null; then break; fi
  sleep 1
done
sleep 3
leftovers=$(pgrep -x tor | wc -l)
sidecar_gone=1
if kill -0 "$SIDE_PID" 2>/dev/null; then sidecar_gone=0; fi
check "stopping the backend leaves no daemon behind" \
  "$([ "$leftovers" = "0" ] && [ "$sidecar_gone" = "1" ] && echo 1 || echo 0)" \
  "leftover tor: $leftovers, sidecar still up: $([ "$sidecar_gone" = "0" ] && echo yes || echo no)"

echo
if [ "$failures" = "0" ]; then
  echo "LINUX RUNTIME PASS — the shipped backend and the shipped Tor serve themselves"
  exit 0
fi
echo "LINUX RUNTIME FAILED: $failures check(s)"
exit 1
