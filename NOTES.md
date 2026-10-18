# Engineering notes

## Status (2026-10-17, evening IST)

**Working and verified:**
- Engine: 81 Python tests.
- Exact reproduction of the reference engine's raw numbers on all 12 synthetic variants (`docs/validation_variants.json`).
- JS/Python assessor parity: 2,454 rows, 0 mismatches.
- Gateway, and all UI screens: Command (WebGL2 plus a Canvas 2D fallback), Steward, Calibrate, Judge, System and Self-test.
- PipeGen's DeepStream app is driven headless by `scripts/pipegen_ctl.sh`.
- The live chain PipeGen → engine → MQTT → gateway → browser has been run on MOT20-02.

**Waiting on a person:**
- The CrowdShield pipeline itself built in PipeGen's GUI (the five stages), with a crowd-capable person or head detector.
- The Pipeline IR and TensorRT speed-report screenshots.
- A measured calibration.
- Hand counts for recall.
- A 5-minute calm recording for the pressure threshold.

## Decisions

1. **The bundle was missing** (`crowdshield_bundle.zip` was never provided). Everything was built from scratch:
   - the engine, gateway, UI and simulator;
   - the contract (`docs/DATA_CONTRACT.md`);
   - the parity recording, which comes from our own browser simulator (`tools/make_parity.mjs`).

   The only shipped data, `crowdshield_variants.zip`, has detections and calibrations but no ground truth. So validation compares against the reference engine's raw rows in `VALIDATION.md`. Our engine matches every one exactly (peak density 0.0 % off, first-Critical time identical).
2. **Pressure density gate = 3 people/m²** (`pressure.min_density`). `VALIDATION.md` uses 3, and 3 is what reproduces the reference numbers (2.0 was also considered); it is configurable.
3. **Status hysteresis.** A rise needs raw above the status for 2 s and goes to the *lowest* level held in that run. A fall needs raw below it for 10 s and goes to the *highest* level held. This is the same in Python and JS.
4. **Risk uses `dmax / red_density`** rather than `dmax / 6`, so the demo profile scales.
5. **Repo location.** The repo stays in the Windows OneDrive folder, which is where the team works. Python runs from WSL using a venv in WSL (`~/.venvs/crowdshield`). PipeGen needs its videos and run files under `/home`:
   - videos: `~/videos/crowd`;
   - run copies and metadata: `~/.crowdshield/run/`;
   - PipeGen projects stay in `~/.pipegen`.
6. **Mosquitto.** The system broker already listens on 127.0.0.1:1883, so the demo uses it. The WebSocket listener on 9001 is optional (the UI talks to the gateway's own WebSocket). `tools/mosquitto_crowdshield.conf` is for machines without a broker.
7. **Gate model.** The gate is a span `y0..y1` on the `x = 0` or `x = width` wall (`gate.x`).
8. **Reality lock** is drawn with Canvas 2D on top of the MJPEG `<img>`, not WebGL. It is an exact per-cell homography warp and is simpler to keep correct.
9. **History.** States are kept at full rate for 120 s, summaries at 2 Hz for 60 min, and grids without tracks at 1 Hz for 60 min. The 1 Hz grids let the time-river rewind redraw the field beyond 120 s.
10. **The classic dashboard** (`/classic`) redirects to the Command screen's Canvas 2D renderer. No separate legacy page exists, because the bundle's dashboard was never provided.
11. **The MOT20-02 calibration** (`config/calibration.json`) was estimated by eye; there is no taped reference. It is labelled in its `note`, in the README and in RESULTS.

## PipeGen automation

- `pipegen` (`/usr/local/bin/pipegen`) only launches the GUI. `pipegen --help` opens the app; only `--version` prints. There is no CLI for the stages.
- The GUI runs in a Docker container, `craftifai-amd64-v3`, from the image `craftifaidocker/deepstream:7.1-amd64-v3`. The container mounts `/home`, `/dev`, `/opt/PipeGen` and `/tmp/.X11-unix`.
- **Projects** live in `~/.pipegen/<user>/<uuid>/` and contain:
  - `RequirementState.json`, `PipelineIR.json`, `state.json`;
  - `model_stage/` with the TensorRT `*.plan`, timing caches and runtime profiles;
  - `pipeline/deepstream/`: `run.sh`, `run.vms.sh`, `run.debug.sh`, `configs/`, `parsers/` (C++ sources and Makefile), `build/` (the `.so` plugins);
  - `output/metadata.json`.
- **The export is a plain `gst-launch-1.0` pipeline.** It uses `nvinfer_backport` (the TensorRT engine) and `nvcustomprocess` plugins. One of them, `libmeta_json.so` (`MetaJson`), writes the per-frame JSON.
  - Output path: `JSON_OUT`.
  - Confidence gate: `JSON_MIN_CONF`.
  - Format: one frame per line, flushed every frame, so the file can be tailed live.
  - Frame record: `{"frame","source_id","pts_ns","width","height","objects":[{"id"|null,"class_id","label","confidence","bbox":{left,top,width,height},"tracker_confidence"}],"shapes":[...],"fields":{...}}`.
  - `id` is the tracker's ID when a tracker precedes the element.
- **VMS mode** (`run.vms.sh`) serves MJPEG on TCP 7010 and newline JSON on TCP 7002. Its defaults need overriding: `VMS_MAX_SHAPES` 64 is too low for crowds (we set 1000) and `VMS_INTERVAL` 5 is too sparse (we set 1).
- **How we drive it.** `scripts/pipegen_ctl.sh` runs a copy of PipeGen's `run.sh` inside the container (`docker exec`). Only the source URI changes (and, with `CS_HEADLESS=1`, the sink becomes `fakesink`). We never edit PipeGen's files.
- **Tested** on the SafeStride project (yolo11s-pose FP16) with MOT20-02. 2,782 frames went through `meta_json` at 25 fps, with tracker IDs. The pose model finds only 3–6 large people per frame, so it is no crowd detector. The CrowdShield project needs a person or head detector.

## Open VERIFY items

- [x] The CrowdShield PipeGen project `a2f60bd6-f599-45b4-85ce-69f431eff9fc`: YOLO11s (COCO) FP16 TensorRT, nvtracker, meta_json + MQTT (`crowdshield/detections`) + display. Exported to `pipegen_export/`. On MOT20-02 it finds 50 people per frame on average (up to 98) with 886 tracker IDs. PipeGen accepted the model with "use it anyway": the catalog has no evidence for dense-crowd performance, which is what the recall curve is for.
- [ ] INT8: PipeGen's quantize step was started but the FP16 baseline was kept (`ModelStage.log`), so there is no INT8 engine yet.
- [x] Evidence: 44 PipeGen screenshots in `evidence/`, numbered by stage (IR graph 13–15, speed report 24, accuracy 23, pipeline running 36–39, ONNX graph 42–44), plus `pipeline_test_run.mp4` (git-ignored). No INT8 report yet.
- [x] `config/default.json` → `pipeline.model` / `pipeline.precision` filled in from PipeGen's Model and Compiler stages.
- [ ] A measured calibration on the demo camera or venue (`/calibrate`).
- [ ] A 5-minute calm recording → `tools/calibrate_pressure.py`.
- [ ] About 30 hand-counted frames → `tools/count_frames.py fit`.
- [x] Run `scripts/pipegen_ctl.sh export` to copy the final export into `pipegen_export/` (engines excluded).
- [ ] The ESP32 gate sign (optional bonus): `firmware/PRD_gate_sign.md`. No board yet.
- [x] The closed loop tested with a SIMULATED sign (`tools/esp32_sim.py`, branch `esp32-sim`):
  - `actuator_test.py` passes, with acknowledgements in 43–47 ms.
  - The synthetic surge drives NONE → SLOW_ENTRY → HOLD_ENTRY on the sign.
  - The dashboard shows "✓ Simulated sign shows …", then "Sign offline for N s" 15 s after the sign stops.
  - Found and fixed while testing:
    - `demo.sh` stopped its jobs with SIGINT, which a script's background jobs ignore, so Ctrl-C left the gateway and engine running. It now uses SIGTERM.
    - The sign acknowledgement stayed green when live data had stopped.
