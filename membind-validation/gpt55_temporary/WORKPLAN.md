# GPT-5.5 Temporary Diagnostic Workplan

This file is maintained with the isolated lane in `gpt55_temporary/`; paths
below are relative to the `membind-validation/` repository unless noted.

This is a temporary diagnostic lane for replacing construction LLM calls with a LabForge/OpenAI-compatible GPT-5.5 chat endpoint. It does not advance V3/V4/V5/V6 and cannot be used as a mainline MemBind/vLLM pass claim.

## Scope

- Run a side-channel Graphiti bottleneck diagnostic while the internal vLLM service is paused.
- Keep the mainline vLLM protocol frozen and untouched.
- Use /chat/completions for GPT-5.5 requests.
- Do not inject additional system prompts; pass through only Graphiti-provided messages.
- Use local_bge_m3 embeddings by default through `gpt55_temporary/scripts/local_embedding_adapter.py`.
- The local embedding adapter uses `BAAI/bge-m3` at revision `5617a9f61b028005a4858fdac845db406aefb181`, cache `/data/predator/ly/Mem/cache/huggingface/hub`, local files only, normalized 1024-dimensional vectors, and CUDA fp16 when available.
- Use User-Agent: OpenAI/Python 1.0.0 for LabForge requests because the default Python urllib user agent is blocked by Cloudflare 1010.
- do not reuse partial v3_smoke_001 cache, prompt cache, embedding cache, or trace files for any pass claim.

## TDD checkpoints

1. Gateway compatibility unit tests: assert API-client User-Agent, classify Cloudflare 1010, classify application-layer 401, and prevent raw key persistence.
2. Adapter contract tests: the temporary Graphiti factory omits vLLM-only extra_body and seed; Graphiti messages are forwarded unchanged; vLLM remains default.
3. Local embedding contract tests: fake SentenceTransformer/torch prove the adapter calls offline BGE-M3 settings, normalizes vectors, exposes Graphiti-compatible create/create_batch, and does not use remote embedding by default.
4. Preflight before live Graphiti: authenticated /models reaches application layer, target model is visible or blocked, minimal /chat/completions succeeds, and local BGE-M3 embedding preflight passes.
5. Live diagnostic: use a fresh gpt55_temporary attempt id, persist status/traces/cache/summary, and stop on first infrastructure or adapter failure.

## Execution guardrails

- Do not change CURRENT_STATE.json to mark V3 passed.
- Do not run M1/V4/V5/V6/future-work lanes from this temporary lane.
- Do not change production .env defaults; pass GPT endpoint settings as CLI arguments to the temporary diagnostic wrapper.
- Keep GPT-specific routing in the temporary Graphiti factory, not in `src/` or shared tests.
- Keep local embedding routing in the temporary Graphiti factory; use `--embedding-provider openai_compatible` only as an explicit fallback.
- Do not mix GPT-5.5 artifacts with the canonical vLLM correctness oracle.
- If GPT-5.5 succeeds, report it as gpt55_temporary_diagnostic, not as a frozen protocol result.

## Artifact layout

- `gpt55_temporary/artifacts/diagnostics/` contains gateway and run summaries.
- `gpt55_temporary/artifacts/tdd/` contains red/green test logs for this lane.
- Mainline `artifacts/` and `CURRENT_STATE.json` are not updated by this lane.

## Next command shape

.venv/bin/python gpt55_temporary/scripts/labforge_gateway_probe.py \\
  --base-url "$OPENAI_BASE_URL" \\
  --api-key "$OPENAI_API_KEY" \\
  --model gpt-5.5 \\
  --output gpt55_temporary/artifacts/diagnostics/labforge_preflight.json

.venv/bin/python gpt55_temporary/scripts/local_embedding_adapter.py \\
  --output gpt55_temporary/artifacts/diagnostics/local_embedding_preflight.json

Only after both preflights are green should a temporary Graphiti diagnostic be started.
