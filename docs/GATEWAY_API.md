# Gateway API

`python -m gateway --config gateway/config.json [--port 8080] [--mqtt localhost:1883|none] [--allow-lan-control]`

The gateway is stdlib asyncio with no web framework. JSON responses over 8 KB are gzipped when the client accepts it, and every `/api/*` response sends `Cache-Control: no-store`. Example responses were taken from a real run: PipeGen video mode on MOT20-02, shortened with `…`.

## Pages

| Path | Page |
|---|---|
| `/` | CrowdShield Command. URL parameters: `?src=gateway|mqtt|sim`, `&scenario=`, `&speed=`, `&theme=day|night`, `&projector=1`, `&renderer=2d`, `&lowpower=1` |
| `/steward` | phone status page |
| `/calibrate` | calibration |
| `/judge` | guided judge mode |
| `/selftest` | in-browser checks of the UI maths |
| `/classic` | redirects to `/?renderer=2d` |
| `/evidence/<file>` | files in `evidence/` (screenshots) |

## REST

```
GET  /api/health
{"v":1,"mqtt":"up","data_age_s":{"state":0.2,"actuator/cmd":0.4,"status":1.0,"alerts":12.1},"stale":false,
 "engine":{"running":true,"pid":3104,"profile":null,"fps":25.0,"latency_ms":null,"source":"video","layout":"pipegen_frame","tracker":"pipegen"},
 "pipegen":{"running":true,"pids":[418],"mode":null,"model":null,"precision":null},
 "gpu":{"name":"NVIDIA GeForce GTX 1650","util":25.0,"mem_used":281.0,"mem_total":4096.0,"temp":60.0,"power_w":…},
 "invalid_messages":0,"clients":1,"uptime_s":1002.0}

GET  /api/state                         the latest crowdshield/state (v1, unchanged), or 404 before the first one
GET  /api/status                        the latest crowdshield/status heartbeat
GET  /api/history?since=<epoch>&fields=summary|full
     summary: 2 Hz summaries for up to 60 min  {"fields":"summary","items":[{"w":…,"t":…,"zone":…,"level":"GREEN","count":20,"dmax":0.476,"pidx":0,…}],"alerts":[…],"now":…}
     full:    complete states for the last 120 s
GET  /api/history?at=<epoch>            {"state": the stored state closest to that time (full in the last 120 s, else the 1 Hz grid without tracks)}
GET  /api/incidents                     {"items":[{"id":"inc-…","opened_w":…,"closed_w":…|null,"zone":…,"peak_level":"RED","peak_dmax":…,"peak_pidx":…,"turbulence":true,"alerts":3,"escalations":[{"w":…,"level":"AMBER"},…]}]}
GET  /api/incidents/<id>                the full incident: summary series from 120 s before opening, and 2 s grid snapshots of the 60 s before each escalation
GET  /api/calibration?plane=floor|head  config/calibration.json (+ "exists")
POST /api/calibration?plane=floor|head  JSON body; validated (4 distinct points, convex, positive sizes), the old file kept as .bak
     → {"ok":true,"path":"config/calibration.json","message":"Saved. The engine reloads it within 2 s."}
     → 400 {"error":"invalid calibration","problems":["image_points must form a convex quadrilateral (click the corners in order)"]}
GET  /api/frame.jpg[?source=auto|upload|video|rtsp|image&t=<s>]   a still frame; never the camera PipeGen uses
POST /api/frame                         body = image bytes (an uploaded snapshot)
GET  /api/frames                        {"upload":false,"video":"/home/dread/videos/crowd/MOT20-02.mp4","rtsp":null,"image":null,"vms":"127.0.0.1:7010","opencv":true}
GET  /api/video.mjpg                    multipart MJPEG: PipeGen VMS video (tcp 7010) if running, else the video file paced in real time, else a still every 2 s
GET  /api/config                        thresholds, pressure, recall, pipeline, demo profile, zone, area, gate, calibration (read-only)
GET  /api/evidence                      {"results_md":…,"results_tables":[…],"validation":{"file":"VALIDATION.md","tables":[…]},
                                         "engine_validation":{"file":"docs/validation_variants.json","rows":[…]},"evidence_files":[…],"incidents":{…},…}
GET  /api/recordings                    {"items":[{"name":"pipegen_pose_mot20-02","kind":"recording","label":"REPLAY","size":996350},
                                                  {"name":"building__low_wall_1080_body","kind":"synthetic","label":"SYNTHETIC",…},…]}
GET  /api/control                       {"actions":{"engine.start:video":["…/python","-m","crowdshield.run","--source","tail:~/.crowdshield/run/metadata.json",…],…},"status":{…},"lan_control":false}
POST /api/control {"action":"engine.start","profile":"synthetic","recording":"surge__pole_720_body"}
                  {"action":"engine.stop"} | {"action":"pipegen.run-video"} | {"action":"pipegen.stop"} | {"action":"pipegen.status"}
GET  /api/invalid                       count and examples of messages that failed validation
```

**Control is allow-listed.** Every command comes from the fixed table in `gateway/config.json`. A request only selects a table entry, plus (for replays) a recording *name* that must exactly match the server's own listing. Commands are argv lists and never go through a shell.

Requests from other devices need `--allow-lan-control` and the 6-digit PIN printed in the gateway terminal, sent as the `X-CS-PIN` header. Calibration saves and snapshot uploads from other devices need the same PIN.

## WebSocket `/ws`

Server push, one JSON object per text frame:

```
{"type":"hello","data":{"v":1,"server":"crowdshield-gateway","prefix":"crowdshield","time":…,"health":{…}}}
{"type":"state","data":<crowdshield/state>}       at most 5 Hz per client; a slow client gets the newest state, never a backlog
{"type":"alert","data":<crowdshield/alerts>}      never dropped
{"type":"status","data":<crowdshield/status>}
{"type":"health","data":<GET /api/health>}         every 2 s
{"type":"incident","data":{…summary…,"event":"opened|escalated|closed"}}
```

The client may send `{"type":"subscribe","topics":["state","alert","health"]}` and `{"type":"ping"}`. The server answers a ping with `{"type":"pong"}`.
