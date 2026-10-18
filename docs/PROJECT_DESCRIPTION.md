# CrowdShield: project description (submission text)

**Track:** A, PipeGen.

## Problem

Crowd crushes build for minutes, in known places, before people die. In Itaewon in 2022, 159 people died in an alley 3.2 m wide at its narrowest. In India, recent events include Hathras 2024, Maha Kumbh 2025 and New Delhi station 2025 (figures in `docs/INCIDENTS.md`).

Stewards see a crowd. They do not see density in people per m², the turbulence that precedes a crush, or how fast the situation is trending.

## What we built

A fixed, elevated camera watches an entry or bottleneck.
1. A PipeGen pipeline detects and tracks every person on the GPU.
2. CrowdShield maps each person to the floor in metres.
3. It computes local density, the flow field and crowd pressure: density × the variance of the local flow, which Johansson, Helbing et al. (2008) found turned turbulent about 10 minutes before the 2006 Mina disaster.
4. It publishes a status (Normal / Building / Critical), a forecast, alerts, an incident log and an advisory gate-sign command.
5. Staff see all of this on CrowdShield Command, a GPU-rendered operator screen, and on steward phones.

Only positions and metrics leave the GPU; no video is stored. CrowdShield advises staff; it never controls gates.

## The PipeGen pipeline

| | |
|---|---|
| Inputs | video file (MOTChallenge MOT20 crowd footage under `~/videos/crowd`); RTSP; USB camera on native Linux |
| Requirement | person detection and multi-object tracking for a dense crowd, TensorRT FP16 (INT8 for comparison), per-frame JSON metadata and MQTT |
| Pipeline IR | nvurisrcbin → nvstreammux → nvinfer_backport (detector) → nvtracker (NvMultiObjectTracker) → meta_json + MQTT publish → nvdsosd display (`pipegen_export/pipeline/deepstream/run.sh`) |
| Model | YOLO11s, COCO person class, from PipeGen's catalog (source "CheckoutMonitoring"), 640 × 640 input |
| Precision | FP16 TensorRT engine: 251 TensorRT layers, 20.3 MB; ONNX check 321 nodes, 0 unresolved (`pipegen_export/model_stage/ModelStage.log`) |
| Speed report | 14.082 ms per frame, about 71 fps, 36.6 W on the GTX 1650 (`evidence/24_speed_report_fp16.jpeg`; the latency is also in `ModelStage.log`) |
| Compile fidelity | FP16 engine vs the FP32 ONNX on our footage (30 samples): mAP@0.5 0.9904, mAP@0.5:0.95 0.9784, precision 0.9903, recall 0.9897 (`evidence/23_accuracy_fp16_vs_fp32.jpeg`) |
| Evidence | Pipeline IR graph: `evidence/13–15_pipeline_ir_graph_*.jpeg`; ONNX check (321 of 323 layers on TensorRT, 0 unresolved): `evidence/18_*.jpeg`, `42_onnx_graph_visualized.jpeg`; the pipeline running with tracked boxes: `evidence/36–39_pipeline_running_*.jpeg`; run video: `evidence/pipeline_test_run.mp4` (not in git, it is 20 MB) |
| Outputs | per-frame JSON (boxes, tracker IDs, pts_ns), consumed live by the CrowdShield engine |

## The decision layer, in plain words

- **Floor mapping:** people are mapped to the floor through a calibration clicked on `/calibrate`.
- **Density:** counted with a smooth 1 m kernel and corrected for people the detector misses in dense crowds. The recall curve comes from hand counts on this camera.
- **Movement:** each person's movement over at least a second gives the flow field.
- **Pressure:** where the local flow keeps changing direction or speed in a dense crowd, crowd pressure rises.
- **Status:** escalates when density or pressure crosses literature thresholds for 2 s, and relaxes only after 10 s.
- **Forecast:** a trend fit warns minutes ahead ("Critical in 1:40 if the trend continues").

## Outputs

- MQTT topics:
  - `crowdshield/state` (5 Hz, grid, tracks, all metrics);
  - `crowdshield/status` (1 Hz, retained);
  - `crowdshield/alerts` (QoS 1);
  - `crowdshield/actuator/cmd` (retained, for the gate sign).
- `incidents.jsonl`: an fsynced incident log.
- Gateway REST and WebSocket.
- Screens:
  - **Command:** a lit density terrain with contours, live flow particles, tracked people, pressure tremor and shock rings, a seismograph, a rewindable time river, and a camera overlay through the inverse homography.
  - **Steward:** a phone page.
  - **Calibrate**.
  - **Judge**.

## Bonuses claimed

None claimed yet.
- **Edge Optimization:** INT8 calibration was started in PipeGen but not built (`evidence/32`, `45`–`47`). We ran out of PipeGen credits.
- **Closed Loop:** we have no ESP32. We wrote the FirmGen PRD (`firmware/PRD_gate_sign.md`), and tested the whole loop with a clearly labelled SIMULATED sign (`tools/esp32_sim.py`):
  - engine command → sign → acknowledgement → dashboard;
  - acknowledgements in under 50 ms;
  - offline detection after 15 s.

  A real board flashed through FirmGen can replace it unchanged.

## Results

See `docs/RESULTS.md` (generated). Highlights from files:
- The engine reproduces the reference engine's numbers exactly on 12 synthetic camera/detector variants (`docs/validation_variants.json`).
- The browser and engine assessors agree on 2,454 of 2,454 steps (`tools/assessor_parity.jsonl`).
- The CrowdShield PipeGen pipeline on MOT20-02: 2,782 frames at 25 fps, on average 50 people per frame (up to 98), 886 tracker IDs, median track 5.5 s, median walking speed 1.34 m/s after the homography (`eval/track_quality_pipegen_yolo11s_fp16_mot20-02.json`). PipeGen's MQTT output delivered all 2,782 frames.

## Honest limitations

- A single camera gives a 2D view. Dense crowds hide people; the recall curve corrects this only as well as the hand counts allow.
- Thresholds are literature starting points. The pressure threshold must be calibrated on a calm recording from the actual camera. Nothing here is a validated safety limit.
- Simulated and synthetic data are used only for development and to demo the interface. They are always labelled, and they say nothing about real crowds.
- The MOT20 calibration was estimated by eye (no tape on that floor).
- CrowdShield is a decision aid for trained staff, not gate control.
