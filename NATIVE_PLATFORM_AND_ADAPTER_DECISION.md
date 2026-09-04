# Native Platform and Adapter Decision

Status: `P2_STRICT_L1_PASSED_L2_PENDING`. Full-history L2 is authorized; formal execution is not yet authorized.

## Formal substrate

All three formal arms share Graphiti `0.29.3` at commit `021d3a57d511f21b10adaf7fa923bd5c1fce5e9d`. The measured runtime must instantiate `graphiti_core.graphiti.Graphiti`, publish through `Graphiti.add_episode`, and use `graphiti_core.llm_client.openai_generic_client.OpenAIGenericClient`, the upstream prompts, upstream Pydantic response models, and upstream extraction, resolution, deduplication, timestamp, and database mutation behavior. Edge extraction retains the upstream `max_tokens=16384` call site.

The formal builder may add only transparent endpoint routing, telemetry, stable logical-request seeds, and the single-attempt transport policy. It may not install finite-pair tasks, edge cursors, response repair, candidate partitioning, bounded response schemas, prompt continuation, endpoint grounding, duplicate recovery, or extraction chunking.

The common input adapter is `MAB_ROLE_AWARE_LOSSLESS_8192_V1`: canonical role-marked sessions are partitioned into contiguous non-overlapping chunks of at most 8192 characters. Turn boundaries are preferred, long turns use whitespace or Unicode-safe code-point boundaries, and concatenation is verified byte-for-byte. Chunk identity remains manifest metadata. Each session has a strict chunk-predecessor chain.

## Deployment decision

P0, `Qwen/Qwen3-8B-AWQ`, is displaced by a deterministic strict full-history failure. In attempt `3758663d769a`, History 0 source 2 chunk 3 reached `extract_nodes.extract_message`, stopped with `finish_reason=length` at 16,384 completion tokens, and returned malformed JSON. The preserved evidence is:

`/data/predator/ly/Mem/experiments/local-qwen3-8b-awq-dualreplica-v1/upstream-l2-full-h0-20260903T160145Z/history-0/replicate-0/GRAPHITI_SERIAL_UPSTREAM_CORE_MAB8192/3758663d769a/failure.json`

P1, `Qwen/Qwen2.5-7B-Instruct-AWQ` at revision `b25037543e9394b818fdfca67ab2a00ecc7dd641`, used `temperature=0.7`, `top_p=0.8`, `top_k=20`, `repetition_penalty=1.05`, JSON-schema output through xgrammar, 16,384 maximum completion tokens, SDK retries disabled, and the stable logical seed shared by A/B/C. It was admitted to strict L1 after the preregistered compatibility replay passed; it was not selected from speed or QA results.

The compatibility replay is:

`/data/predator/ly/Mem/experiments/local-qwen25-7b-awq-dualreplica-v1/p1-qwen25-compatibility-20260903T163742Z/MODEL_COMPATIBILITY_REPLAY.json`

Its file SHA-256 is `139b5bad3925d0531384235004cc2a004ef3654052b43c7e69344433cc09f020`; its authenticated payload SHA-256 is `91e04d1312eef0cdcd354bd6894c589be21cec1f1dcd0d0328f7f82012c665da`.

That replay did not qualify the strict runtime. It used isolated witnesses and missed the growing previous-episode context that later caused P1 attempt `1107077ed04e` to fail at source 3 chunk 2. Exact strict L1 reconstructed the real request from the preserved 39-node, 59-relationship, 13-episode namespace, intercepted it before publication, and proved that every historical request identity matched. It then submitted that wire request exactly once with no provider retries. The result again had 12,979 prompt tokens, `finish_reason=length`, 16,384 completion tokens, invalid JSON/Pydantic/schema output, and response-content SHA-256 `5da6bb84f5a7d7486757e4cc60f450a3018645cd0088c76117aa244976d64174`, identical to the historical failure. The namespace remained unchanged. P1 is therefore rejected by a deterministic deployment compatibility failure; P1 L1 must not be repeated and P1 L2 must not start. Terminal evidence is under:

`/data/predator/ly/Mem/experiments/local-qwen25-7b-awq-dualreplica-v1/strict-upstream-l1-exact-20260904T092622Z`

The single next deployment hypothesis was P2, `Qwen/Qwen3-14B-AWQ` at revision `31c69efc29464b6bb0aee1398b5a7b50a99340c3`. The complete local snapshot uses the model's official non-thinking sampling: `temperature=0.6`, `top_p=0.95`, and `top_k=20`. P2 keeps Graphiti prompts, upstream response schemas, call graph, MAB8192, stable logical seeds, `max_tokens=16384`, zero SDK retries, routing semantics, dataset, and evaluator unchanged. No grid search or fallback mutation was used.

The first P2 pre-measurement startup proved that the inherited GPU1 prepare allocation was too small: vLLM exposed 5.92 GiB KV memory versus 6.25 GiB required for one 40,960-token request. Before any P2 measured attempt, the shared prepare allocation was increased from 0.70 to 0.72 while embedding remained 0.25. The revalidated profile provides 69,760 native and 41,888 prepare KV tokens, passes both structured probes and the embedding probe, and is sealed at `/data/predator/ly/Mem/profiles/local-qwen3-14b-awq-dualreplica-v1/platform_manifest.20260904T095114Z.25205c46578a.json`, payload SHA-256 `25205c46578ac0954e121b909030aa07ede9871d8f8fc6818da21414e51ecaa0`.

Exact strict L1 then submitted the authenticated growing-history edge request once with unchanged messages, upstream `ExtractedEdges` schema, stable seed, and `max_tokens=16384`; only the declared P2 deployment fields differed. It returned `finish_reason=stop` with 12,983 prompt tokens, 6 completion tokens, and JSON/Pydantic/schema-valid output. No repair or retry occurred and the preserved namespace was unchanged. The terminal result is `/data/predator/ly/Mem/experiments/local-qwen3-14b-awq-dualreplica-v1/strict-upstream-l1-exact-20260904T095439Z/L1_RESULT.json`, file SHA-256 `7b80d8b8e9e887693a9ffb8c73967573d58dbceedd5def27687974c27986c24c`. This selects P2 for full-history L2 only; formal execution remains gated on three complete L2 cells and the finalizer.

The rejected P1 profile is `local-qwen25-7b-awq-dualreplica-v1`, with two identical single-GPU RTX 3090 Ti LLM replicas, `max_model_len=65536`, and static YaRN settings `factor=2.0`, `original_max_position_embeddings=32768`. Its authenticated platform manifest remains historical evidence at `/data/predator/ly/Mem/profiles/local-qwen25-7b-awq-dualreplica-v1/platform_manifest.20260903T163542Z.6c10750e6988.json`, payload SHA-256 `6c10750e6988944a0a31e08552ae8a4958fcec3f086560a20d78b119d7bb147c`. It is not a formal platform after the exact L1 failure.

## Evidence boundary

Graphiti pinned source defines the native call graph and response models. Qwen documentation and generation configuration support deployment settings. AutoSchemaKG supplies public precedent for Qwen2.5-7B extraction and 8192-character non-overlapping chunks. MemoryAgentBench supplies the official dataset and evaluator authority. These sources support configuration choices only; none proves that P2 succeeds, that MemBind is faster, or that quality is preserved. `MAB_ROLE_AWARE_LOSSLESS_8192_V1` is a shared project adapter, not an upstream Graphiti or MemoryAgentBench interface.
