# Real footage and data sources

| File | Source | Licence | Use |
|---|---|---|---|
| `~/videos/crowd/MOT20-02.mp4`, `MOT20-03.mp4`, `MOT20-01.mp4` | MOTChallenge MOT20 sequence preview videos, https://motchallenge.net/data/MOT20/ (downloaded by `scripts/fetch_videos.sh`) | MOTChallenge data are distributed for research under CC BY-NC-SA 3.0. Check the site's terms before any public or commercial use. | Video input to the PipeGen pipeline for development and the hackathon demo, with attribution on screen. Not committed. |
| `~/videos/crowd/MOT17-04-SDP.mp4` | MOTChallenge MOT17 preview video | as above | as above |
| `recordings/pipegen_pose_mot20-02.jsonl` | Our recording of PipeGen's output (box positions and track IDs only) on MOT20-02 | derived data, positions only | Replay and tracking-quality evaluation |
| `samples/pipegen_safestride_pose_mot20-02.json` | The first frames of PipeGen's `meta_json` output on MOT20-02 | derived data | Adapter test on the real format |

The calibration for MOT20-02 (`config/calibration.json`) was estimated by eye from the frame, without any measured reference on that floor. Treat densities from that clip as approximate.
