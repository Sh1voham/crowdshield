# Formulas

Every tunable value lives in `config/default.json`. All algorithms use frame timestamps, never the wall clock; `tests/test_static.py` enforces this.

## 1. Floor position (`crowdshield/geometry.py`)

A homography `H` comes from four image↔floor point pairs, computed with a normalised DLT (Hartley). It maps image pixels to floor metres. Which pixel stands on the floor depends on the foot mode:

| `foot_mode` | Pixel mapped through `H` |
|---|---|
| `bbox_bottom` (body detector) | bottom centre of the box `(cx, y2)` |
| `head_offset` (head detector, floor calibration) | `(cx, y2 + head_k · box_h)`, with `head_k` 6.5 by default |
| `head_plane` (head detector, head-plane calibration) | the box centre, mapped through a homography clicked on heads at the taped corners |

Detections at a different resolution from the calibration frame are rescaled first. Points more than 0.5 m outside the area are dropped.

## 2. Velocity (`kinematics.py`)

`v = (p(t) − p(t − Δ)) / Δ`, with Δ ≥ 1 s taken from the track's own history (the newest sample at least 1 s old). The result is then smoothed with an exponential moving average, `α = 1 − exp(−dt / 1 s)`.

Tracks younger than 0.5 s have no velocity. They still count for density.

## 3. Fields on a 1 m grid (`fields.py`, Johansson et al. 2008)

```
rho(r)   = 1/(pi R^2) * sum_j exp(-|r_j - r|^2 / R^2)          R = 1 m, cut-off 3R
V(r)     = sum_j w_j v_j / sum_j w_j                            null if sum w < 1 (people with a velocity only)
speed(r) = sum_j w_j |v_j| / sum_j w_j                          null if sum w < 0.3
```

The kernel integrates to one person (`tests/test_fields.py::test_kernel_integrates_to_one`).

## 4. Crowd pressure

Each cell keeps 8 s of `V(r, t)` samples, which is 40 samples at the 5 Hz update rate.

```
if valid samples >= 15 and rho(r) >= 3 people/m^2:
    Var_t = mean_t |V - mean_t V|^2
    P(r)  = rho(r) * Var_t          [s^-2]
else P(r) = 0
pidx = max_r P(r) / P_thr           P_thr = 0.02 s^-2 by default; calibrate on a calm recording
```

The paper's text describes "variance of speeds multiplied by density". We use the temporal variance of the locally averaged velocity instead. The spread of individual speeds, and tracker noise, would flag ordinary walking. Uniform steady flow gives P = 0, and oscillating flow gives P > 0 (tested).

The paper's 0.02 s⁻² depends on the setup. `tools/calibrate_pressure.py` proposes `max(0.02, 3 × P99)` from a calm recording on this camera.

## 5. Recall correction

`rho_corr = rho_meas / r(rho_meas)`. Here `r` is a piecewise-linear recall curve over **measured** density, or a constant `recall_estimate` (default 1.0).

The correction is never iterated on corrected density, because iterating overshoots. The curve is fitted from hand counts with `tools/count_frames.py fit`, which uses `fields.fit_recall_curve`: binned mean recall by measured density.

## 6. Status, turbulence, forecast and risk (`assess.py` = `ui/js/sim/assessor.js`)

```
raw = RED   if dmax >= 6 or (pidx >= 1   and dmax >= 4)
      AMBER if dmax >= 4 or (pidx >= 0.5 and dmax >= 3)
      GREEN otherwise
rise: raw above the status for 2 s  -> the lowest level seen in that run
fall: raw below the status for 10 s -> the highest level seen in that run
turbulence on : pidx >= 1 and dmax >= 3 for 3 s (since = start of the run); off: pidx < 0.8 for 5 s
forecast: least squares of dmax on t over the last 90 s (>= 20 samples spanning >= 30 s)
          if slope > 0.0008/s and fitted_now >= 2 and R^2 >= 0.4:
              t_amber = (4 - fitted_now)/slope, t_red = (6 - fitted_now)/slope   (dropped beyond 900 s)
          INFO:FORECAST once when t_red < 300 s and the status is not RED; re-armed when the forecast clears
risk = clip(0.55 min(1, dmax/red) + 0.30 min(1, pidx) + 0.15 clip(trend_per_min, 0, 1), 0, 1)
action = HOLD_ENTRY (RED) | SLOW_ENTRY (AMBER) | NONE (GREEN)
```

The demo profile scales the density gates to 1.5 and 2.5 people/m² (`demo_profile` in the config).

## Validated numbers

These come from `docs/validation_variants.json`; see `docs/RESULTS.md`. On the 12 synthetic camera/detector variants, the engine matches the reference engine's `raw` rows exactly:
- peak density: 0.0 % difference;
- first Critical: identical times;
- datasets that never reach Critical: identical.

They are synthetic data. They show the formulas and code are right, not that the thresholds are right for real crowds.
