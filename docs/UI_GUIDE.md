# Reading CrowdShield Command

## The screen

| Area | What it shows |
|---|---|
| **Header** | The zone name, and a data label: LIVE, VIDEO via PipeGen, REPLAY, SYNTHETIC or SIMULATED, plus DEMO PROFILE when the low thresholds are on. Also data age, pipeline fps (from frame timestamps) and latency (only when the source carries absolute time). A warning chip appears below 10 fps. |
| **Status slab** | The status word with its shape: a circle with a tick (Normal), a triangle (Building) or an octagon (Critical). Then how long it has held, the advice in plain words, peak density, pressure as a multiple of its threshold, people in view, mean speed, the forecast sentence, and the gate sign exactly as the ESP32 shows it (with its acknowledgement when one is connected). |
| **Field** | The calibrated floor in 3D. **Height and warmth** show people per m² (0.22 m of height per person/m²). **Thin contours** mark every 1 /m²; **thick contours** with labels mark the Building and Critical levels. **Streaks** follow the measured flow: longer means faster, brighter means denser. **Points of light** are tracked people with 3 s trails; hover over one for its PipeGen track ID and speed. **Trembling surface and expanding rings** mark crowd pressure above its threshold, labelled with the multiple. The **gate** glows in the colour of the current advice. The diamond marks the peak cell and its value. |
| **Seismograph** | Pressure × threshold over the last 5 minutes, with the Building band (0.5×) and the Critical band (1×) shaded. Peak density is a separate trace below; the two are never on one axis. Alert ticks run along the bottom, and a dashed line marks each escalation. Hover for exact values. |
| **Time river** | The whole session. Height is people and colour is status; stretches that were not live are drawn faded. Drag to replay, or press ← → while it has focus. Triangles mark incidents; click one to replay the 60 s before the escalation at 4×. **Live** returns to now. |

## Signature moments

All four are driven by real events. None of them loops, and none hides the status.

1. **Escalation:** when the status rises to Critical, the view eases toward the epicentre (1.2 s) and the rest of the stage dims. The octagon stamps in, the sign flips pixel by pixel to HOLD ENTRY, and the seismograph draws an event line. After about 5 s it settles back.
2. **Forecast horizon:** while Critical is forecast within 5 minutes, a wireframe "future surface" shows the projected density at that time, with a countdown.
3. **Reality lock** (`R` or **Camera**): the same contours, rings, people marks and gate drawn on the camera image through the inverse homography. Without a video stream it uses a still frame, labelled "Still frame".
4. **Rewind:** drag the river; the field, people and seismograph show that moment.

## Keyboard

| Key | Action |
|---|---|
| `1`–`5` | Command, Incidents, System, Calibrate, Judge |
| `L` | Back to live, or replay the last minute |
| `R` | Reality lock |
| `T` | Night or day theme |
| `P` | Projector mode (thicker lines, bigger type) |
| `M` | Reduced motion (static streamlines, no tremor or camera moves) |
| `E` | Low power (the UI capped at 15 fps) |
| `?` | Help |

## Stale data

If no state arrives for 3 s:
- a striped band reads "No live data for N s";
- the status greys out and reads "last known";
- the sign shows NO DATA;
- the steward page says the same.

The screen never shows a confident Normal on stale data.

## Design notes

- **The night stage is the hero theme:** deep ink blue `#0d1524` with a vignette. Light comes from the data: the terrain ramps from the stage colour to tungsten white `#ffe7c4`, and the contours are ice blue `#8ec5ff`.
- **Status colours are reserved:** `#33d17a`, `#ffb51f` and `#ff4a55`. They always appear with a shape and a word, and are never used for decoration.
- **The daylight survey theme** (steward and calibrate pages, or `T`) uses cool-green paper `#e8efea`, graphite ink and burnt-sienna contours.
- **Type:**
  - Barlow Condensed (SIL OFL 1.1), a face derived from road signage, for numbers and status words;
  - Atkinson Hyperlegible (SIL OFL 1.1, Braille Institute) for sentences.

  Both are self-hosted in `ui/fonts/` with their licences.
- **Field rendering:** raw WebGL2, with no libraries and no CDN.
  - The terrain uses a Catmull-Rom upsampled density texture with normals and ambient occlusion.
  - Contours are computed in the fragment shader, and their labels come from marching squares in a worker.
  - Particles are advected by transform feedback, with a decaying trail buffer.
  - MSAA plus bloom applies to the emissive layers only.
  - The Canvas 2D fallback draws the same layers from above (`?renderer=2d`).
