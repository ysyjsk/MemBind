# V7 Runner Handoff

The V7 implementation is currently theory/reference plus observer-only. The
current repository head does not equal the methodology pin, and the P7
native-continuation contract is still `UNKNOWN`; consequently no M0/M1/M2
method-selection seal authorizes a live treatment.

The runner is nevertheless ready for the final GPU handoff:

```bash
cd saturated_fixed_work_baseline_v1_3
export SILICONFLOW_API_KEY='...'
PYTHONPATH=src ../membind-validation/.venv/bin/python scripts/run_v7_live.py \
  --output-root v7/artifacts/gpu-dry-run-001 \
  --run-id v7-gpu-dry-run
```

This is provider-free and writes a redacted `RUN_MANIFEST.json`. Do not put
the key in a config file, command-line argument, source file, or artifact.

An actual live call is intentionally blocked until a sealed
`METHOD_SELECTION.json` has `authorized: true`, a selected `M0`, `M1`, or `M2`
method, and `treatment_authorized: true`. The command also requires an
explicit adapter:

```bash
PYTHONPATH=src ../membind-validation/.venv/bin/python scripts/run_v7_live.py \
  --live --method M1 --gate v7/METHOD_SELECTION.json \
  --adapter your_gpu_adapter:run \
  --output-root v7/artifacts/gpu-live-001 --run-id v7-gpu-live-001
```

The adapter is called only after the gate and key checks. It receives the
validated `V7LiveConfig`; it must use the pinned native Graphiti continuation,
record the frozen observer schema, preserve provider/embedding epochs, and
write its own sealed evidence. The runner does not implement a hidden replay,
repair, persistence Apply, or native-demand skip.

The SiliconFlow endpoint is OpenAI-compatible and defaults to
`https://api.siliconflow.cn/v1`; override it with the explicit URL flags when
the GPU deployment requires a different endpoint. The key is never returned by
`redact_config` and is not written to `RUN_MANIFEST.json`.
