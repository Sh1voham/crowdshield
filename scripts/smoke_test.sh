#!/usr/bin/env bash
# No-camera smoke test: replay tools/sample_stream.jsonl (SIMULATED) into the gateway, check
# /api/state and /api/health, and (if mosquitto_pub exists) push a few messages through MQTT.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
PY="${CS_PY:-$HOME/.venvs/crowdshield/bin/python}"; [ -x "$PY" ] || PY="$(command -v python3)"
PORT="${PORT:-8099}"
"$PY" -m gateway --port "$PORT" --mqtt none --replay-jsonl tools/sample_stream.jsonl --replay-speed 20 > .cs_run/smoke_gateway.log 2>&1 &
GW=$!
trap 'kill $GW 2>/dev/null || true' EXIT
for _ in $(seq 1 40); do curl -s -o /dev/null "http://127.0.0.1:$PORT/api/health" && break; sleep 0.25; done
sleep 2
STATE=$(curl -s "http://127.0.0.1:$PORT/api/state")
echo "$STATE" | "$PY" -c "import json,sys; s=json.load(sys.stdin); print('state ok:', s['type'], 'level', s['status']['level'], 'people', s['count'], 'source', s['pipeline']['source'])"
curl -s "http://127.0.0.1:$PORT/api/health" | "$PY" -c "import json,sys; h=json.load(sys.stdin); print('health ok: invalid messages', h['invalid_messages'], 'data age', h['data_age_s'])"
curl -s -o /dev/null -w "Command page: HTTP %{http_code}\n" "http://127.0.0.1:$PORT/"
if command -v mosquitto_pub >/dev/null 2>&1; then
  head -n 5 tools/sample_stream.jsonl | while read -r line; do
    t=$(echo "$line" | "$PY" -c "import json,sys; print(json.load(sys.stdin)['topic'])")
    p=$(echo "$line" | "$PY" -c "import json,sys; print(json.dumps(json.load(sys.stdin)['payload']))")
    mosquitto_pub -h localhost -t "smoketest/$t" -m "$p" && echo "published to MQTT: smoketest/$t"
  done
fi
echo "SMOKE TEST PASS"
