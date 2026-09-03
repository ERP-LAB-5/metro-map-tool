#!/usr/bin/env bash
# Start the metro-map designer, creating the virtualenv on first run and
# replacing any instance already holding the port.
# Ubuntu 24.04 is PEP-668 managed, so Flask has to live in a venv, not system pip.
#
#   ./run.sh                 start (or restart) on 127.0.0.1:8765
#   ./run.sh --port 9000     somewhere else
#   ./run.sh --stop          shut the running one down and exit
set -euo pipefail
cd "$(dirname "$0")"

PORT=8765
BIND=127.0.0.1
STOP_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --host) BIND="$2"; shift 2 ;;
    --stop) STOP_ONLY=1; shift ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "  ! unknown option $1"; exit 2 ;;
  esac
done

stop_designer() {
  # ask it to close itself first, so a save in flight can finish
  curl -fsS -X POST "http://127.0.0.1:$PORT/api/shutdown" >/dev/null 2>&1 || true
  sleep 0.4
  # "nothing is listening" is the normal case, not a failure — keep set -e happy
  local holder
  holder=$(ss -ltnp 2>/dev/null | grep ":$PORT " | grep -o 'pid=[0-9]*' |
           cut -d= -f2 | head -1 || true)
  if [ -n "${holder:-}" ]; then
    echo "  killing pid $holder"
    kill "$holder" 2>/dev/null || true
    sleep 0.4
    kill -9 "$holder" 2>/dev/null || true
  fi
  # wait for the port to come free, so the restart does not race the old socket
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    ss -ltn 2>/dev/null | grep -q ":$PORT " || return 0
    sleep 0.3
  done
  echo "  ! port $PORT is still held — start somewhere else with --port"
  return 1
}

if [ "$STOP_ONLY" = 1 ]; then
  stop_designer
  echo "  stopped"
  exit 0
fi

if [ ! -x .venv/bin/python ]; then
  echo "  creating .venv ..."
  python3 -m venv .venv
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -r requirements.txt
fi

stop_designer
echo "  starting designer on http://$BIND:$PORT"
exec .venv/bin/python -m metro_map_tool.app --host "$BIND" --port "$PORT"
