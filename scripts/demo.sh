#!/usr/bin/env bash
# One-command CrowdShield demo (run inside WSL / Linux, from anywhere).
#
#   scripts/demo.sh video [VIDEO]       PipeGen's DeepStream app on a video (a live PipeGen run) -> engine -> UI
#   scripts/demo.sh live                engine on whatever PipeGen is running now (started from PipeGen's GUI Run stage)
#   scripts/demo.sh vms [VIDEO]         PipeGen VMS mode: TCP metadata + MJPEG video for reality lock
#   scripts/demo.sh replay NAME         a recording in recordings/NAME.jsonl (labelled REPLAY)
#   scripts/demo.sh synthetic NAME      a synthetic dataset, e.g. surge__pole_720_body (labelled SYNTHETIC)
#   scripts/demo.sh sim                 the UI's built-in simulator only (labelled SIMULATED)
#
# Options (env): PROFILE=demo  PORT=8080  CS_PY=python  PIPEGEN_PROJECT=<uuid>  CS_HEADLESS=1
#                CS_SIGN_SIM=1  also run the SIMULATED ESP32 gate sign (tools/esp32_sim.py; no hardware)
# Ctrl-C stops everything this script started.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
MODE="${1:-video}"; shift || true
ARG="${1:-}"
PORT="${PORT:-8080}"
PY="${CS_PY:-$HOME/.venvs/crowdshield/bin/python}"
[ -x "$PY" ] || PY="$(command -v python3)"
PROFILE="${PROFILE:-default}"
RUN="$HOME/.crowdshield/run"
export CS_LOOP="${CS_LOOP:-1}"   # loop the video so the demo never runs dry
mkdir -p "$RUN" "$REPO/.cs_run" "$REPO/recordings"
PIDS=()

cleanup() {
  echo; echo "stopping..."
  # TERM, not INT: background jobs of a script inherit SIGINT as ignored, so Python never sees Ctrl-C
  for p in "${PIDS[@]:-}"; do [ -n "$p" ] && kill -TERM "$p" 2>/dev/null || true; done
  if [ "${STARTED_PIPEGEN:-0}" = "1" ]; then bash "$REPO/scripts/pipegen_ctl.sh" stop || true; fi
  wait 2>/dev/null || true
  echo "stopped."
}
trap cleanup INT TERM EXIT

# WSL may silently drop connections to closed ports, so never wait more than a second
port_open() { timeout 1 bash -c "exec 3<>/dev/tcp/127.0.0.1/$1" 2>/dev/null; }

# 1. quick checks
echo "== CrowdShield demo: mode $MODE, profile $PROFILE"
command -v nvidia-smi >/dev/null && nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total --format=csv,noheader || echo "GPU: nvidia-smi not found"
# 2. broker
if port_open 1883; then echo "broker: using the MQTT broker already on :1883"
elif command -v mosquitto >/dev/null; then
  mosquitto -c "$REPO/tools/mosquitto_crowdshield.conf" > "$REPO/.cs_run/mosquitto.log" 2>&1 & PIDS+=($!)
  sleep 0.5; echo "broker: started mosquitto on :1883 and :9001"
else echo "broker: none (install with: sudo apt install -y mosquitto mosquitto-clients); continuing without MQTT"; fi
if port_open "$PORT"; then echo "port $PORT is busy: stop the other gateway or set PORT=..."; exit 1; fi

# 3. engine
ENGINE_ARGS=(--calibration config/calibration.json --profile "$PROFILE" --incidents "$REPO/incidents.jsonl" --quiet)
case "$MODE" in
  video)
    STARTED_PIPEGEN=1
    bash "$REPO/scripts/pipegen_ctl.sh" run-video ${ARG:+"$ARG"}
    SRC="tail:$RUN/metadata.json"; LABEL=video ;;
  vms)
    STARTED_PIPEGEN=1
    bash "$REPO/scripts/pipegen_ctl.sh" run-vms ${ARG:+"$ARG"}
    SRC="tcp://127.0.0.1:7002"; LABEL=video ;;
  live)
    SRC="tail:$RUN/metadata.json"; LABEL=live ;;
  replay)
    [ -f "recordings/$ARG.jsonl" ] || { echo "no recordings/$ARG.jsonl"; ls recordings; exit 1; }
    SRC="jsonl:recordings/$ARG.jsonl"; LABEL=replay; ENGINE_ARGS+=(--realtime) ;;
  synthetic)
    D="data/synthetic/variants/${ARG:-surge__pole_720_body}"
    [ -d "$D" ] || { echo "no dataset $D"; ls data/synthetic/variants; exit 1; }
    CAL="$D/calibration.json"; [ -f "$D/calibration_head.json" ] && CAL="$D/calibration_head.json"
    SRC="jsonl:$D/detections_ds_minimal.jsonl.gz"; LABEL=synthetic
    ENGINE_ARGS=(--calibration "$CAL" --profile "$PROFILE" --incidents "$REPO/.cs_run/incidents_synthetic.jsonl" --quiet --realtime) ;;
  sim) SRC="" ;;
  *) echo "unknown mode $MODE"; exit 1 ;;
esac
if [ -n "${SRC:-}" ]; then
  "$PY" -m crowdshield.run --source "$SRC" --source-label "$LABEL" "${ENGINE_ARGS[@]}" > "$REPO/.cs_run/engine.log" 2>&1 & PIDS+=($!)
  echo "engine: $SRC (log .cs_run/engine.log)"
fi

if [ "${CS_SIGN_SIM:-0}" = "1" ]; then
  "$PY" "$REPO/tools/esp32_sim.py" > "$REPO/.cs_run/esp32_sim.log" 2>&1 & PIDS+=($!)
  echo "gate sign: SIMULATED ESP32 (log .cs_run/esp32_sim.log)"
fi

# 4. gateway + UI
"$PY" -m gateway --port "$PORT" > "$REPO/.cs_run/gateway.log" 2>&1 & PIDS+=($!)
for _ in $(seq 1 40); do port_open "$PORT" && break; sleep 0.25; done
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
Q=""; [ "$MODE" = "sim" ] && Q="?src=sim&scenario=${ARG:-building}"
echo
echo "  Command    http://localhost:$PORT/$Q"
echo "  Steward    http://${IP:-localhost}:$PORT/steward     (phones: forward the port from Windows, see README)"
echo "  Calibrate  http://localhost:$PORT/calibrate"
echo "  Judge      http://localhost:$PORT/judge"
grep -m1 "Control PIN" "$REPO/.cs_run/gateway.log" 2>/dev/null || true
if [ -f "$RUN/project" ]; then
  echo; echo "  PipeGen project: $(cat "$RUN/project")"; echo "  PipeGen run script: $RUN/run.sh"
fi
if command -v cmd.exe >/dev/null 2>&1; then cmd.exe /c start "" "http://localhost:$PORT/$Q" >/dev/null 2>&1 || true
elif command -v xdg-open >/dev/null 2>&1; then xdg-open "http://localhost:$PORT/$Q" >/dev/null 2>&1 || true; fi
echo; echo "Ctrl-C stops everything."
wait
