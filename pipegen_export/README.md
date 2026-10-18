# PipeGen export

This folder receives a copy of the CrowdShield PipeGen project. Run:

```bash
bash scripts/pipegen_ctl.sh export        # copies the newest PipeGen project (or PIPEGEN_PROJECT=<uuid>)
```

The copy contains:
- `RequirementState.json`, `PipelineIR.json` and `state.json`: the five stages;
- `model_stage/` profiles and reports;
- `pipeline/deepstream/`: `run.sh`, `run.vms.sh`, configs, and the parser and plugin sources.

**Excluded:**
- TensorRT engines (`*.plan`, `*.engine`): they are tied to the GPU and TensorRT version, and they exceed 50 MB.
- ONNX models.
- `build/` outputs.

To rebuild an engine, open the project in PipeGen and re-run the Compiler stage (FP16 or INT8). `run.sh` rebuilds the plugin `.so` files with `make` on start.

CrowdShield never edits these files. `scripts/pipegen_ctl.sh` runs a copy of `run.sh` in which only the video source (and optionally the on-screen sink) is replaced.
