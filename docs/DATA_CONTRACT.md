# CrowdShield data contract (v 1)

Every message is UTF-8 JSON with `"v": 1`. Units are SI: metres, seconds, people per m² and s⁻² for pressure. A missing or not-computable value is `null`; NaN is never sent. Floor coordinates have their origin at the top-left corner of the calibrated area: `x` grows to the right along `width_m`, `y` grows downward along `height_m`. Grid cells are row-major, `grid.density[row][col]`, and cell `(row, col)` has its centre at `((col + 0.5) · cell_m, (row + 0.5) · cell_m)`.

The topic prefix defaults to `crowdshield` (`--topic-prefix`).

| Topic | Producer | Rate | QoS / retain | Payload |
|---|---|---|---|---|
| `crowdshield/detections` | PipeGen (input) | per frame | 0 | PipeGen's own format; see [Input](#input) |
| `crowdshield/state` | engine | 5 Hz | 0 | [state](#state) |
| `crowdshield/status` | engine | 1 Hz | 1, retained | [status](#status) |
| `crowdshield/alerts` | engine | on event | 1 | [alert](#alert) |
| `crowdshield/actuator/cmd` | engine | on change and every 5 s | 1, retained | [actuator command](#actuator-command) |
| `crowdshield/actuator/state` | ESP32 gate sign | on each command | 1 | [actuator state](#actuator-state) |

## Status levels

| Level | Label | Shape (UI) | Advisory `action` | Sign text |
|---|---|---|---|---|
| `GREEN` | Normal | circle with a tick | `NONE` | ENTRY OPEN |
| `AMBER` | Building | triangle | `SLOW_ENTRY` | SLOW ENTRY |
| `RED` | Critical | octagon | `HOLD_ENTRY` | HOLD ENTRY |

CrowdShield **advises trained staff**. `action` is a recommendation and the sign is an advisory display; nothing in this contract opens or closes a gate.

The rules (`crowdshield/assess.py`, thresholds in `config/default.json`):

```
pidx = pressure.max / pressure.threshold
raw  = RED   if dmax >= 6 or (pidx >= 1   and dmax >= 4)
       AMBER if dmax >= 4 or (pidx >= 0.5 and dmax >= 3)
       GREEN otherwise
rise to a higher level after raw stays above the status for 2 s; fall after it stays below for 10 s
```

The demo profile (`--profile demo`) lowers the density thresholds to 1.5 and 2.5 people per m², which suits a taped square with a few volunteers. The zone name then carries the suffix ` (demo profile)`.

## state

Published at 5 Hz. Here is an example with arrays shortened:

```json
{
  "v": 1, "type": "state", "seq": 618,
  "t": 123.6, "ts": "2026-10-18T10:02:03.600Z",
  "zone": "Gate A",
  "area": {"width_m": 24, "height_m": 12},
  "gate": {"x": 24, "y0": 4.9, "y1": 7.1, "label": "Gate A"},
  "status": {"level": "AMBER", "label": "Building", "since_t": 119.6, "raw": "RED", "cause": "DENSITY"},
  "action": "SLOW_ENTRY",
  "count": 214,
  "density": {"max": 5.41, "mean": 0.743, "p95": 3.92, "peak_cell": [5, 22], "measured_max": 5.41, "unit": "persons/m2"},
  "pressure": {"max": 0.0182, "mean": 0.0031, "threshold": 0.02, "index": 0.91},
  "speed": {"mean": 0.37},
  "turbulence": {"on": false, "since_t": null},
  "forecast": {"trend_per_min": 0.42, "t_amber_s": null, "t_red_s": 84.0, "r2": 0.81},
  "risk": 0.71,
  "thresholds": {"amber_density": 4.0, "red_density": 6.0, "amber_pidx": 0.5, "red_pidx": 1.0,
                 "amber_pidx_density": 3.0, "red_pidx_density": 4.0, "p_thr": 0.02, "profile": "default"},
  "grid": {"cell_m": 1.0, "rows": 12, "cols": 24,
           "density": [[0.0, 0.12]], "pressure": [[0.0, 0.0]], "speed": [[null, 0.81]],
           "vx": [[null, 0.62]], "vy": [[null, -0.05]]},
  "tracks": [{"id": 1012, "x": 21.37, "y": 5.12, "vx": 0.41, "vy": -0.03}],
  "pipeline": {"source": "live", "fps": 29.8, "latency_ms": 41.0, "frames": 3712, "model": null,
               "precision": null, "layout": "pipegen_frame", "tracker": "pipegen"},
  "actuator": {"device": "esp32-sign-1", "ack_seq": 12, "shown": "SLOW_ENTRY", "rssi": -61, "uptime_s": 1834, "age_s": 0.4}
}
```

- `t` is stream time in seconds: the first frame is t = 0, and t never decreases. All algorithms use `t`.
- `ts` is wall-clock time. It is the receive time for live data. In replays it is `--start-wallclock + t`.
- `density.max` is the recall-corrected peak cell density and `density.measured_max` is the uncorrected value. `density.mean` is `count / area`.
- `pressure.max` is in s⁻². `pressure.index` is `pressure.max / pressure.threshold`.
- `forecast.*` is `null` when the trend fit is not valid. `t_amber_s` and `t_red_s` are seconds from now; a level already reached gives `null`.
- `risk` is in 0..1: `0.55·min(1, dmax/red) + 0.30·min(1, pidx) + 0.15·clip(trend_per_min, 0, 1)`.
- `grid` values are rounded to 3 decimals for density, speed, vx and vy, and to 5 decimals for pressure. A cell with too little weight has `null` for `speed`, `vx` and `vy`.
- `tracks` holds at most 400 entries, rounded to 2 decimals. `vx` and `vy` are `null` for tracks younger than 0.5 s. `id` is PipeGen's tracker ID, or the fallback tracker's ID when `pipeline.tracker` is `"fallback"`.
- `pipeline.source` is one of `live`, `video`, `replay`, `synthetic` or `sim`. Anything other than `live` and `video` must be labelled as such by every display.
- `pipeline.fps` is measured from frame timestamps. `pipeline.latency_ms` is `null` unless the source carries an absolute timestamp. `pipeline.model` and `pipeline.precision` come from config and are filled in from PipeGen; they are never guessed.
- `actuator` is the latest `crowdshield/actuator/state`, or `null` when none has arrived. `age_s` is measured on the wall clock.
- `camera` is the calibration the engine is using: `image_points`, `world_points`, `frame_size` and `foot_mode`. Displays use it to project the analysis onto the camera image and to look from the camera's side. It is an additive field.

## status

A compact heartbeat, published at 1 Hz and retained:

```json
{"v": 1, "type": "status", "t": 123.0, "ts": "2026-10-18T10:02:03.000Z", "zone": "Gate A",
 "level": "AMBER", "label": "Building", "since_t": 119.6, "action": "SLOW_ENTRY",
 "count": 214, "dmax": 5.41, "pidx": 0.91, "risk": 0.71, "turbulence": false,
 "source": "live", "data_age_s": 0.1, "stale": false}
```

`stale` is `true` once no frame has arrived for more than 3 s (wall clock). In that case `level` is the last known level, and displays must grey it out.

## alert

Published when an event happens, with QoS 1. It is also appended to `incidents.jsonl` as one JSON line, flushed and fsynced.

```json
{"v": 1, "type": "alert", "id": "a-000003", "t": 149.6, "ts": "2026-10-18T10:02:29.600Z", "zone": "Gate A",
 "level": "RED", "kind": "PRESSURE", "status": "RED", "action": "HOLD_ENTRY",
 "message": "Critical: crowd pressure 1.1× threshold at 5.8 people/m². Hold entry. Open all exits. Alert supervisors.",
 "metrics": {"dmax": 5.81, "pidx": 1.12, "count": 241, "risk": 0.86}, "cell": [5, 22]}
```

| `level:kind` | When |
|---|---|
| `AMBER:DENSITY` / `AMBER:PRESSURE` | the status becomes AMBER (rising or falling); the kind tells which rule holds |
| `RED:DENSITY` / `RED:PRESSURE` | the status becomes RED |
| `CLEAR:RECOVERED` | the status returns to GREEN |
| `RED:TURBULENCE` | turbulence starts: pidx ≥ 1 and dmax ≥ 3 for 3 s. It ends when pidx < 0.8 for 5 s, with no alert |
| `INFO:FORECAST` | the trend predicts Critical within 300 s while the status is not RED. It re-arms once the forecast clears |
| `AMBER:PIPELINE` / `CLEAR:PIPELINE` | no frame for more than 3 s (wall clock), and recovery |

## actuator command

Retained, with QoS 1. Published on every change and repeated every 5 s, so a sign that reboots shows the current advice within 5 s.

```json
{"v": 1, "seq": 12, "action": "SLOW_ENTRY", "level": "AMBER", "text": "SLOW ENTRY", "zone": "Gate A",
 "t": 119.6, "ts": "2026-10-18T10:01:59.600Z", "advisory": true}
```

`seq` increases on each change and stays the same on repeats.

## actuator state

The ESP32 sign publishes this within 500 ms of each command:

```json
{"v": 1, "device": "esp32-sign-1", "ack_seq": 12, "shown": "SLOW_ENTRY", "rssi": -61, "uptime_s": 1834}
```

If no command arrives for 15 s, the sign shows a distinct SYSTEM OFFLINE pattern, so a failure is visible on the sign.

The simulated sign (`tools/esp32_sim.py`) sends `"device": "esp32-sim"`, `"rssi": null` (it has no radio) and `"simulated": true`. The dashboard then says "Simulated sign". A real board omits `simulated`.

## Input

PipeGen's DeepStream app writes per-frame metadata. `crowdshield/io_pipegen.py` is the only code that reads it. It accepts these layouts and logs which one it detected:

| Layout | Where it comes from | Shape |
|---|---|---|
| `pipegen_frame` | PipeGen 0.5.1 `meta_json` (`output/metadata.json`, one frame per line, flushed per frame) | `{"frame","source_id","pts_ns","width","height","objects":[{"id","class_id","label","confidence","bbox":{"left","top","width","height"},"tracker_confidence"}]}` |
| `pipegen_frame` (shapes) | pose or render pipelines | `"shapes":[{"kind":"box","x","y","w","h","id","score","label"}]` |
| `pipegen_vms` | PipeGen VMS mode (`run.vms.sh`; newline JSON on TCP 7002, and `store/events/cam<N>.jsonl`) | `{"t": <epoch s>, "frame", "shapes":[...]}` |
| `ds_minimal` | DeepStream nvmsgconv minimal schema over MQTT | `{"@timestamp","id","objects":["trackId\|left\|top\|right\|bottom\|label"]}` |
| `ds_full` | DeepStream full schema, one object per message | `{"@timestamp","id","object":{"id","bbox":{"topleftx",...}}}` |
| `generic` | any list under `objects`, `detections`, `boxes` or `persons` | `bbox` given as `[x1,y1,x2,y2]`, `[x,y,w,h]` or `{left,top,width,height}` |

- **Time:** `pts_ns` (ns), `t` (epoch s), `fields.timestamp_s`, `@timestamp` (ISO), or `timestamp`/`ts`/`pts`. A numeric timestamp's unit is inferred from the median frame interval. If no timestamp exists, the adapter uses `frame / default_fps`.
- **Coordinates:** values ≤ 1.5 are treated as normalised and need `width`/`height` or `--frame-size`.
- **Resolution:** detections are rescaled to the calibration's `frame_size` when the stream size differs.
