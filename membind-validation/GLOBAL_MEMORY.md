# MemBind Global Memory

This file is a compact map for future agents. It records what is safe to resume, what must remain separate, and which files explain each active lane.

## Mainline validation memory

- CURRENT_STATE.json is the machine-readable mainline state. It currently represents the frozen validation sequence and must not be advanced by temporary GPT diagnostics.
- EXPERIMENT_PLAN.md is the execution-facing plan for the frozen vLLM/Qwen validation lane.
- ../MemBind_CURRENT_VALIDATION_PLAN_v1.2.md is the current authoritative human-readable overlay for stage ordering and gates.
- The mainline vLLM protocol remains frozen. Temporary GPT/LabForge artifacts are not V3/V4/V5/V6 pass evidence.

## Temporary GPT/LabForge memory

- `gpt55_temporary/README.md` explains the boundary and test command.
- `gpt55_temporary/WORKPLAN.md` describes the temporary diagnostic lane and its guardrails.
- `gpt55_temporary/scripts/labforge_gateway_probe.py` is a standalone gateway probe; it is outside `src/` and the shared scripts tree.
- `gpt55_temporary/scripts/gpt55_temporary_graphiti_probe.py` gates one M0 live diagnostic behind LabForge preflight, owns the temporary Graphiti factory, and writes only temporary summaries.
- `gpt55_temporary/scripts/local_embedding_adapter.py` is the temporary local BGE-M3 adapter. It uses `BAAI/bge-m3` with the frozen revision and `/data/predator/ly/Mem/cache/huggingface/hub`; it keeps embedding fallback logic out of `src/`.
- `gpt55_temporary/tests/test_labforge_gateway_probe.py` locks the LabForge compatibility rule: use an OpenAI-style User-Agent and never persist raw credentials.
- `gpt55_temporary/tests/test_local_embedding_adapter.py` locks the local embedding contract with fake torch/SentenceTransformer modules so no GPU/model is required for unit tests.
- `gpt55_temporary/tests/test_workplan.py` keeps the temporary workplan visible and prevents future agents from treating GPT diagnostics as mainline passes.
- `gpt55_temporary/tests/test_lane_isolation.py` and the root isolation guard test protect the filesystem and `CURRENT_STATE.json` boundary.
- Mainline `src/` and shared tests must not contain GPT55/LabForge/openai_chat markers; temporary compatibility logic belongs under `gpt55_temporary/`.

## Known paused artifacts

- v3_smoke_001 is an interrupted V3 attempt caused by remote model shutdown. It is not a correctness result.
- Do not reuse partial v3_smoke_001 prompt cache, embedding cache, or trace files for a pass claim.
- The pause report is artifacts/diagnostics/v3_smoke_001_pause_report_20260808.md.

## Maintenance notes

- New diagnostic scripts should include a module docstring explaining whether they are mainline or temporary.
- New tests should include a short class docstring when they protect protocol boundaries rather than ordinary implementation details.
- Diagnostics may persist short credential fingerprints but must never persist raw API keys or Authorization headers.
