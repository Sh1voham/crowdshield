#!/usr/bin/env bash
# Control the DeepStream app that PipeGen generated, without the GUI.
#
#   scripts/pipegen_ctl.sh status
#   scripts/pipegen_ctl.sh run-video [PATH]       # file source (default: CS_VIDEO or ~/videos/crowd/MOT20-02.mp4)
#   scripts/pipegen_ctl.sh run-rtsp URL
#   scripts/pipegen_ctl.sh run-camera DEV         # USB camera (not visible inside WSL)
#   scripts/pipegen_ctl.sh run-vms [PATH]         # PipeGen VMS mode: MJPEG :7010 + newline JSON :7002
#   scripts/pipegen_ctl.sh stop | logs | export
#
# It never edits PipeGen's install or project files. It runs PipeGen's own generated
# run.sh inside PipeGen's container (that is where nvinfer_backport and the TensorRT
# engine live). To switch the source it runs a COPY of run.sh with only the source
# URI replaced, kept in ~/.crowdshield/run/. Metadata goes to ~/.crowdshield/run/metadata.json
# (JSON_OUT, read by `python -m crowdshield.run --source tail:...`).
#
# Environment:
#   PIPEGEN_PROJECT   project UUID or path (default: the newest project with a pipeline)
#   PIPEGEN_CONTAINER container name (default: craftifai-amd64-v3)
#   CS_HEADLESS=1     replace the on-screen video sink with fakesink
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTAINER="${PIPEGEN_CONTAINER:-craftifai-amd64-v3}"
PG_ROOT="${PIPEGEN_HOME:-$HOME/.pipegen}"
RUN_DIR="${CS_RUN_DIR:-$HOME/.crowdshield/run}"
META="$RUN_DIR/metadata.json"
LOG="$RUN_DIR/pipegen.log"
mkdir -p "$RUN_DIR" "$REPO/.cs_run"

die() { echo "pipegen_ctl: $*" >&2; exit 1; }

find_project() {
  if [ -n "${PIPEGEN_PROJECT:-}" ]; then
    if [ -d "$PIPEGEN_PROJECT" ]; then echo "$PIPEGEN_PROJECT"; return; fi
    local p; p=$(find "$PG_ROOT" -maxdepth 2 -type d -name "$PIPEGEN_PROJECT" | head -1)
    [ -n "$p" ] && { echo "$p"; return; }
    die "project $PIPEGEN_PROJECT not found under $PG_ROOT"
  fi
  # newest project that has a generated DeepStream run.sh
  local best="" best_t=0 f t
  while IFS= read -r f; do
    t=$(stat -c %Y "$f")
    if [ "$t" -gt "$best_t" ]; then best_t=$t; best="$(dirname "$(dirname "$(dirname "$f")")")"; fi
  done < <(find "$PG_ROOT" -maxdepth 5 -path '*/pipeline/deepstream/run.sh' 2>/dev/null)
  [ -n "$best" ] || die "no PipeGen project with pipeline/deepstream/run.sh under $PG_ROOT (finish the Compiler stage in PipeGen first)"
  echo "$best"
}

container_ok() { docker exec "$CONTAINER" true >/dev/null 2>&1; }

make_runner() {  # $1 = template (run.sh or run.vms.sh), $2 = source uri or "" to keep
  local proj="$1" tmpl="$2" uri="$3" dir="$proj/pipeline/deepstream"
  [ -f "$dir/$tmpl" ] || die "$dir/$tmpl not found (this PipeGen export has no $tmpl)"
  local out="$RUN_DIR/$tmpl"
  # keep PipeGen's script byte for byte, except: DIR points at the export, and optionally the source URI and sink
  sed -e "s#^DIR=.*#DIR=\"$dir\"#" "$dir/$tmpl" > "$out"
  if [ -n "$uri" ]; then
    sed -i -E "s#uri=\"[^\"]*\"#uri=\"$uri\"#" "$out"
  fi
  if [ "${CS_HEADLESS:-0}" = "1" ]; then
    sed -i -E 's#fpsdisplaysink[^!]*$#fakesink sync=1#; s#nveglglessink#fakesink#g' "$out"
  fi
  chmod +x "$out"
  echo "$out"
}

start() {  # $1 = template, $2 = uri
  container_ok || die "PipeGen's container $CONTAINER is not running. Start PipeGen (run: pipegen) and retry."
  local proj; proj=$(find_project)
  stop_quiet
  local runner; runner=$(make_runner "$proj" "$1" "$2")
  : > "$META"
  echo "$proj" > "$RUN_DIR/project"
  date +%s.%N > "$RUN_DIR/started_at"
  cp "$RUN_DIR/started_at" "$REPO/.cs_run/pipegen_started_at" 2>/dev/null || true
  echo "pipegen_ctl: project $proj"
  echo "pipegen_ctl: running $runner (source: ${2:-as generated}) in $CONTAINER"
  echo "pipegen_ctl: metadata -> $META ; log -> $LOG"
  rm -f "$RUN_DIR/stop"
  # CS_LOOP=1 restarts PipeGen's app when a video ends (meta_json rewrites the file; the engine follows)
  local loop="${CS_LOOP:-0}"
  docker exec -d -e DISPLAY="${DISPLAY:-:0}" -e JSON_OUT="$META" -e JSON_SOURCE="${2:-generated}" \
    -e VMS_MAX_SHAPES="${VMS_MAX_SHAPES:-1000}" -e VMS_INTERVAL="${VMS_INTERVAL:-1}" -e VMS_MIN_CONF="${VMS_MIN_CONF:-0.3}" \
    -e VMS_ROOT="$RUN_DIR/vms" \
    "$CONTAINER" bash -c "while :; do bash '$runner' >> '$LOG' 2>&1; [ '$loop' = 1 ] || break; [ -f '$RUN_DIR/stop' ] && break; sleep 1; done"
}

stop_quiet() {
  container_ok || return 0
  touch "$RUN_DIR/stop"
  docker exec "$CONTAINER" pkill -INT -f "gst-launch-1.0" >/dev/null 2>&1 || true
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    docker exec "$CONTAINER" pgrep -f "gst-launch-1.0" >/dev/null 2>&1 || return 0
    sleep 0.5
  done
  docker exec "$CONTAINER" pkill -KILL -f "gst-launch-1.0" >/dev/null 2>&1 || true
}

cmd="${1:-status}"; shift || true
case "$cmd" in
  status)
    echo "container: $(container_ok && echo running || echo 'not running')"
    proj=$(find_project 2>/dev/null || true)
    echo "project:   ${proj:-none}"
    [ -n "$proj" ] && ls "$proj/pipeline/deepstream" 2>/dev/null | sed 's/^/  /'
    if container_ok && docker exec "$CONTAINER" pgrep -f gst-launch-1.0 >/dev/null 2>&1; then echo "pipeline:  running"; else echo "pipeline:  stopped"; fi
    [ -s "$META" ] && echo "metadata:  $META ($(wc -l < "$META") lines)" || echo "metadata:  none yet"
    ;;
  run-video)
    src="${1:-${CS_VIDEO:-$HOME/videos/crowd/MOT20-02.mp4}}"
    src="$(readlink -f "$src")"
    case "$src" in /home/*) ;; *) die "PipeGen's container only mounts /home: put the video under /home (got $src)";; esac
    [ -f "$src" ] || die "video not found: $src"
    start run.sh "file://$src"
    ;;
  run-vms)
    src="${1:-${CS_VIDEO:-$HOME/videos/crowd/MOT20-02.mp4}}"
    src="$(readlink -f "$src")"
    start run.vms.sh "file://$src"
    ;;
  run-rtsp)
    [ -n "${1:-}" ] || die "usage: run-rtsp rtsp://HOST:PORT/PATH"
    start run.sh "$1"
    ;;
  run-camera)
    dev="${1:-/dev/video0}"
    [ -e "$dev" ] || die "$dev not found. WSL does not see laptop cameras: use run-rtsp (e.g. a phone camera app) or run-video."
    start run.sh ""   # PipeGen's camera pipelines read CAM / CAM_W / CAM_H from the environment
    ;;
  stop)
    stop_quiet; echo "pipeline stopped"
    ;;
  logs)
    tail -n "${1:-60}" "$LOG"
    ;;
  export)
    # copy the PipeGen export into the repo (engines over 50 MB stay out of git)
    proj=$(find_project)
    dest="$REPO/pipegen_export"
    mkdir -p "$dest"
    tar -C "$proj" --exclude='*.plan' --exclude='*.plan.key' --exclude='*.engine' --exclude='*.onnx' --exclude='*.cache' \
      --exclude='./.cache' --exclude='./output' --exclude='*/build' --exclude='*/.verify_frames' --exclude='*/store' \
      --exclude='.run_output.txt' --ignore-failed-read -cf - . 2>/dev/null | tar -C "$dest" -xf -
    echo "copied $proj -> $dest (TensorRT engines and ONNX excluded; rebuild them in PipeGen's Compiler stage)"
    ;;
  *) die "unknown command $cmd (status|run-video|run-vms|run-rtsp|run-camera|stop|logs|export)";;
esac
