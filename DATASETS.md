# CrowdShield datasets

Two kinds of data, never mixed up in claims:

1. **Synthetic data shipped in this bundle.** It has known ground truth and tests the code and formulas. It says nothing about real crowds.
2. **Real public datasets to download.** Use them for realistic tests and to make the demo credible. Check each licence before showing their footage in a public demo or committing it to a public repo.

## 1. Synthetic datasets (in `data/synthetic/`)

All at 10 frames per second, with floor coordinates in metres. Trajectories come from the CrowdShield simulators (the dashboard's JavaScript sim for the first four, `tools/crowd_sim.py` for the rest). Detections come from `tools/make_synthetic_detections.py`. Results are in `data/synthetic/VALIDATION.md`.

| Scenario | Area (m) | Length (s) | Peak people | What it tests | True worst status |
|---|---|---|---|---|---|
| `calm` | 24 × 12 | 90 | 39 | Normal flow, nothing should fire | Normal |
| `counterflow` | 24 × 6 | 150 | 106 | Two opposing streams; high velocity variety, moderate density. Pressure false-alarm test | Normal |
| `crossing` | 20 × 20 | 120 | 128 | Four streams crossing. False-alarm test for density over-correction | Normal |
| `temple_queue` | 24 × 12 | 180 | 118 | Serpentine queue between barricades, steady moderate density | Normal |
| `seated_dense` | 24 × 12 | 120 | 444 | Dense **stationary** crowd plus passing walkers. Building is allowed, turbulence must not fire | Building |
| `building` | 24 × 12 | 180 | 268 | Queue builds at a 2.2 m gate until critical | Critical |
| `stop_and_go` | 24 × 12 | 180 | 294 | Gate opens 12 s and closes 8 s: stop-and-go waves | Critical |
| `platform` | 30 × 8 | 150 | 189 | Railway platform, doors open only 70 to 100 s | Critical |
| `evacuation` | 24 × 12 | 120 | 420 | Everyone leaves through two 1.6 m exits | Critical |
| `surge` | 24 × 12 | 75 | 533 | Sudden surge at a 1.8 m gate | Critical |
| `relief` | 24 × 12 | 60 | 280 | Packed crowd disperses when gates open | Critical, then Normal |

Files in each scenario folder:

* `truth.jsonl.gz`: every person's true floor position and velocity, every frame.
* `labels.jsonl.gz`: the true status timeline at 5 Hz (density max, pressure index, status, turbulence), computed from the truth with the reference formulas. Use it to score any engine.
* `detections_ds_minimal.jsonl.gz`: camera detections in a DeepStream-minimal-like layout (`"tid|left|top|right|bottom|label"`).
* `samples/ds_full.jsonl`, `samples/generic.jsonl`, `samples/normalized.jsonl`: 30-frame samples in three other layouts, for adapter tests.
* `calibration.json` (exact), `calibration_clicked.json` (a taped rectangle with 2 px click error), and `calibration_head.json` (head-detector variants only).
* `frame.png`: a rendered camera view, for testing the calibration page.
* `detector_stats.json`, `detection_log.jsonl.gz` and `scenario.json`.

**Camera and detector variants** (in `variants/`, shipped as a separate zip). These cover `building`, `surge` and `platform`, each with:

* `low_wall_1080_body`: a 4 m wall camera, strong perspective and heavy occlusion (recall 0.74 to 0.84).
* `overhead_1080_body`: 18 m, looking almost straight down (recall 0.94 to 0.96).
* `pole_720_body`: the default pole camera at 1280 × 720.
* `pole_1080_head`: a head detector, mapped to the floor through a head-plane homography. On site you get that by clicking the head of an average-height person standing on each taped corner.

Regenerate any of it:

```bash
python tools/crowd_sim.py --all
python tools/make_synthetic_detections.py data/synthetic/<scenario> [--camera pole|low_wall|overhead] [--res 1080|720] [--detector body|head] [--out DIR]
python reference/validate_synthetic.py        # about 5 minutes on 2 cores
```

## 2. Real public datasets (download on the demo machine)

| Dataset | What it is | Why it helps | Where | Licence |
|---|---|---|---|---|
| **Jülich pedestrian dynamics data archive** (Forschungszentrum Jülich) | Trajectories from controlled experiments. The CroMa project alone ran 7 experiments with about 1000 participants: train platform, crowd management, single-file, personal space, boarding and alighting, bottleneck, tiny box | **Real measured trajectories at real densities.** Convert them to `truth.jsonl` and run the same validation, or render them through `make_synthetic_detections.py` | Archive DOI 10.34735/ped.da; experiment pages such as `ped.fz-juelich.de/da/2021bottleneck`, `/2021train_platform`, `/2021crowd_management` | Check the archive page; the data-guidance paper itself is CC BY 4.0 |
| **PedPy** (Python library) | Analysis of pedestrian trajectories: classic, Voronoi and Gaussian density and speed profiles; loads Jülich archive files | An independent cross-check of the density formulas on real trajectories | `pedpy.readthedocs.io` | Check the project page |
| **Lyon Festival of Lights field study (2022, MADRAS project)** | About 7,000 trajectories from a real dense festival crowd, up to 4 people per m², plus GPS traces and a catalogue of crowd phenomena | Real, uncontrolled dense crowd; good for "does it behave sensibly" checks | arXiv:2410.05288 (see the paper for data access) | Not stated on the abstract page; check the paper |
| **MOT20** | 8 video sequences of very crowded scenes, 25 fps, mostly 1920×1080, with tracking ground truth in image coordinates | Dense real **video** to run through PipeGen; ground-truth boxes let you measure detector recall | `motchallenge.net/data/MOT20` | Not stated on the page we checked; read MOTChallenge's terms before using it in a demo |
| **CroHD / Head Tracking 21 (HT21)** | 9 sequences, 11,463 frames, 2,276,838 annotated heads; train stations, Shibuya crossing, a stadium entrance at night; 1920×1080, 25 fps, elevated views | The densest real footage. Tests head detectors and the head-plane method | `motchallenge.net/data/Head_Tracking_21` | Not stated on the page we checked; read the terms |
| **CrowdHuman** | Image benchmark for detecting people in crowds (full-body, visible-body and head boxes) | Choosing or validating a detector in PipeGen's Deep Search | arXiv:1805.00123 and the official site | Not confirmed here; commonly distributed for non-commercial research. Check before use |

How to use real data with CrowdShield:

1. **Real trajectories (Jülich, Lyon):** convert to `{"t","people":[[id,x,y,vx,vy],...]}` (velocities from positions over ≥ 1 s), give them a `scenario.json` with the area, then render detections with `make_synthetic_detections.py` and score with `validate_synthetic.py`. This is the strongest test available without a live crowd, and the results are honest because the motion is real.
2. **Real video (MOT20, HT21):** play it through **PipeGen** as a video-file source, record the detections, calibrate the floor from visible features if possible (state the uncertainty), and measure detector recall against the ground-truth boxes in density bands. That gives a realistic recall curve for that camera type.
3. Always write the source and licence of every real clip in `data/real/SOURCES.md`, and label it on screen in the demo.
