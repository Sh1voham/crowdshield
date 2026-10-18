#!/usr/bin/env bash
# Download public dense-crowd clips for PipeGen video runs (REAL footage, not ours).
#   MOTChallenge MOT20 / MOT17 sequence preview videos (motchallenge.net).
#   Licence: MOTChallenge datasets are distributed for research under CC BY-NC-SA 3.0;
#   check https://motchallenge.net before any public or commercial use. Videos are
#   git-ignored and must be credited on screen when shown (see data/real/SOURCES.md).
# Files land in ~/videos/crowd (PipeGen wants videos under /home) as H.264 MP4.
# Conversion uses ffmpeg on the host if present, otherwise the ffmpeg inside the PipeGen
# container (which mounts /home), otherwise OpenCV from the CrowdShield venv.
set -euo pipefail
DEST="${DEST:-$HOME/videos/crowd}"
CONTAINER="${PIPEGEN_CONTAINER:-craftifai-amd64-v3}"
VENV_PY="${CS_VENV:-$HOME/.venvs/crowdshield}/bin/python"
mkdir -p "$DEST"
cd "$DEST"
rm -f -- "-raw.webm"

convert() {  # $1 = input webm, $2 = output mp4
  if command -v ffmpeg >/dev/null 2>&1; then
    ffmpeg -loglevel error -y -i "$1" -c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p -an "$2" && return 0
  fi
  if docker exec "$CONTAINER" true >/dev/null 2>&1; then
    for enc in libx264 h264_nvenc; do
      if docker exec "$CONTAINER" ffmpeg -loglevel error -y -i "$DEST/$1" -c:v "$enc" -pix_fmt yuv420p -an "$DEST/$2"; then
        return 0
      fi
    done
  fi
  echo "no ffmpeg with H.264; falling back to OpenCV mp4v (MPEG-4 Part 2)" >&2
  "$VENV_PY" - "$1" "$2" <<'EOF'
import sys, cv2
src, dst = sys.argv[1], sys.argv[2]
cap = cv2.VideoCapture(src)
fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
ok, frame = cap.read()
h, w = frame.shape[:2]
out = cv2.VideoWriter(dst, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
while ok:
    out.write(frame)
    ok, frame = cap.read()
out.release()
EOF
}

for SEQ in MOT20-02 MOT20-03 MOT20-01 MOT17-04-SDP; do
  if [ ! -s "${SEQ}.mp4" ]; then
    if [ ! -s "${SEQ}-raw.webm" ]; then
      echo "downloading ${SEQ} ..."
      curl -sfL --retry 3 --max-time 900 -o "${SEQ}-raw.webm" "https://motchallenge.net/sequenceVideos/${SEQ}-raw.webm"
    fi
    echo "converting ${SEQ} to mp4 ..."
    convert "${SEQ}-raw.webm" "${SEQ}.mp4"
  fi
  "$VENV_PY" -c "import cv2,sys; c=cv2.VideoCapture(sys.argv[1]); print(sys.argv[1], int(c.get(3)), 'x', int(c.get(4)), round(c.get(5),2), 'fps', int(c.get(7)), 'frames')" "${SEQ}.mp4"
done
ls -la "$DEST"
