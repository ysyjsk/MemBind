# Clean MemBind

This directory is the proposed mainline for MemBind after the Qwen3/vLLM P2
diagnostic. It is intentionally small: method-only core, a thin upstream
Graphiti boundary, the frozen public workload adapter, backend identities,
and a few experiment hooks. Current validation status is
`NATIVE_NOT_READY`: the clean method is testable, but no local OSS model has
yet passed the long-state Graphiti structured-output check.

The historical implementation and every old artifact stay in the repository as
legacy/evidence. They are not imported by this package.

## Quick checks

```bash
cd clean_membind
python -m pytest
PYTHONPATH=src python -m membind.experiment.native_smoke --json
```

The smoke command is configuration-only until the external validation has
recorded a real Ollama model digest and Neo4j identity. It never pretends that
an unavailable provider is a successful construction.

See `PROJECT_AUDIT.md`, `NATIVE_DECISION.md`, and
`MAIN_EXPERIMENT_PLAN.md` for the evidence and conditional freeze conditions.
