# Demo script

## Before the judges arrive (10 min)

1. In WSL, run `python3 scripts/doctor.py`. The GPU, PipeGen and its container must pass.
2. Start PipeGen (`pipegen`) and open the CrowdShield project. Keep the Pipeline IR graph and the speed report ready to show.
3. Run `bash scripts/demo.sh video ~/videos/crowd/MOT20-02.mp4` and open http://localhost:8080/ on the projector. Press `P` for projector mode.
4. Open `/steward` on a phone (port forwarding: see README) and tap "Turn on vibration and sound".
5. Have the `/judge` tab ready.

## Part 1: PipeGen is the eyes (60 s)

Show PipeGen: Requirement → Pipeline IR → Model → Compiler (the TensorRT speed report) → Run.

Point to the Command header: "VIDEO via PipeGen", 25 fps from frame timestamps.

Open the System screen with `S`. It shows PipeGen's pipeline running, plus GPU use and memory from `nvidia-smi`.

## Part 2: the field (90 s)

1. Walk through the terrain: height and warmth show people per m², thick contours mark Building and Critical, and the streaks are the measured flow.
2. Hover over a person to show their PipeGen track ID and speed.
3. Press `R` for reality lock. The same contours and people marks sit on the camera image.
4. Then switch to the escalation. Run `scripts/demo.sh synthetic surge__pole_720_body`, which is labelled SYNTHETIC on screen. As the crowd builds:
   - the forecast ghost appears ("Critical in 0:08");
   - when Critical arrives, the camera frames the epicentre, the stage dims, the octagon stamps in, the LED sign flips to HOLD ENTRY and the steward phone buzzes;
   - shock rings show crowd pressure above its threshold.
5. Drag the time river back to rewind and show the incident replay.

## Part 3: honesty and evidence (45 s)

Open `/judge`, step 5, Proof:
- the engine matches the reference engine on 12 synthetic datasets;
- the browser and engine assessors agree exactly;
- the real PipeGen run's tracking quality, with the file named under each table.

Then step 6, Limits: advisory only, a 2D camera, thresholds calibrated on this camera, no video stored.

Stop the engine (`Ctrl-C` in the demo terminal). Within 4 s the "No live data" band appears and the sign shows NO DATA.

## 90-second pitch

> Crowd crushes don't come out of nowhere. In Itaewon, people died in an alley 3.2 metres wide after a crowd built for hours. In Mina in 2006, researchers saw the crowd's motion turn turbulent about ten minutes before the disaster. CrowdShield watches for exactly that.
>
> A PipeGen pipeline runs on this GTX 1650: a TensorRT person detector and tracker inside DeepStream. It finds and follows every person in the camera view. CrowdShield maps them onto the floor in metres and measures three things: how many people per square metre, how the crowd is flowing, and crowd pressure, meaning density times how erratically the flow moves.
>
> When those cross literature thresholds, stewards get Building or Critical, with a plain instruction: slow entry, or hold entry and open all exits. The gate sign shows the same advice, and a trend forecast warns minutes ahead.
>
> Everything you see is driven by real numbers, the dense ridge, the flow, the shock rings, and anything simulated is labelled. No video is stored. It advises people; it never locks a gate.

## Likely questions

- **Why not just count people?** Count alone misses what kills: local density in one spot, and turbulent motion. We compute both on a 1 m grid.
- **How accurate is the density?** Dense crowds hide people from the detector. We measure recall by hand-counting frames by density band and correct with that curve. The synthetic variants show how much the camera height changes it.
- **Are the thresholds validated?** No. They are literature starting points (4 and 6 /m², pressure 0.02 s⁻²) calibrated on this camera. We say so on the Limits slide.
- **What does PipeGen do here?** All perception: detection, tracking and the TensorRT engine are PipeGen's generated DeepStream app, which our scripts run as is. CrowdShield has no neural network.
- **Latency?** The speed report shows PipeGen's per-frame timing. The engine updates the fields at 5 Hz, and the UI pushes at 5 Hz.

## Fallbacks

| If… | Do this |
|---|---|
| PipeGen's pipeline fails | `scripts/demo.sh replay pipegen_pose_mot20-02`: the recorded real PipeGen output, labelled REPLAY |
| WebGL is unavailable | Add `?renderer=2d` to the URL |
| Nothing else works | `http://localhost:8080/?src=sim&scenario=surge`: the simulator, labelled SIMULATED |
