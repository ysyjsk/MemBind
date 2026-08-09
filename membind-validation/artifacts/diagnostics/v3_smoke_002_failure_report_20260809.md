# V3 smoke 002 failure report

Generated: 2026-08-09

## Outcome

- Stage: V3 full correctness smoke
- Attempt: `v3_smoke_002`
- Instance: `c6853660`
- Result: blocked during M0 capture
- M2: not started
- M1: forbidden and not started
- Mainline construction: Qwen3-32B-FP8 through vLLM 0.26.0
- GPT-5.5 temporary code/artifacts: excluded and not used

The attempt is retained as a failed attempt. Its prompt and embedding caches
are partial capture state and MUST NOT be reused for a correctness pass.

## First divergence

The M0 trace contains two episode rows. Source sequence `0` completed; source
sequence `1` entered `add_episode` but failed during structured LLM extraction
before embedding or publication. The first failed operation is therefore the
construction LLM response, not M2 replay, retrieval, or database commit.

The failure was:

```text
JSONDecodeError: Expecting property name enclosed in double quotes
line 1 column 21457 (char 21456)
```

The retained failure record shows eight structured parse failures for the same
request trajectory. The shared bounded budgets were both exhausted:

| budget | finish reason | prompt tokens | completion tokens | raw length |
|---:|---|---:|---:|---:|
| 2048 | `length` | 5795 | 2048 | 5386 |
| 8192 | `length` | 5795 | 8192 | 21456 |

The response repeated `Sagebrush` entries and ended inside the JSON array. No
additional retry is permitted by the frozen protocol.

## Gate evidence

- M0 run status: `failed`
- M0 `llm_call_count`: 14
- M0 `structured_request_count`: 10
- M0 `structured_parse_failures`: 8
- M0 `structured_response_failures`: 4
- M0 unexpected prompt: `false`
- M0 embedding oracle misses: `0`
- M0 post-run Neo4j node count: `0`
- M0 cross-encoder rank calls: `0`
- M2 run artifact: not created
- V3 summary `ok`: `false`

The service preflight immediately before the attempt was green for vLLM
0.26.0, `max_model_len=40960`, the requested construction model, the requested
embedding model/dimension 1024, and an empty local Neo4j database. Thus this
attempt is not classified as a service-down interruption. The current evidence
supports an upstream/model structured-output failure under the frozen Graphiti
request, but does not prove whether the root cause is vLLM guided-decoding
configuration, model behavior, or an upstream request/runtime interaction.

Historical smoke artifacts contain other length-truncated structured responses
at later source sequences. They are supporting failure evidence only; they do
not authorize changing the frozen prompt, schema, model parameters, or retry
budget.

## Artifact hashes

```text
artifacts/smoke/v3_smoke_002.json
  61d208d7289f64dbc4bd0dda0cdb48c748087e6b3c11a75ff4c95f2aa08b2fa9
artifacts/runs/v3_smoke_v3_smoke_002_M0_c6853660.json
  981aae0e157f60541c306bc7ff292606a6631257643b67c237eada422876a09e
artifacts/llm_failures/v3_smoke_v3_smoke_002_M0_c6853660.json
  718e09c45f10744f2a1a7a7027a37df23566bf21716ace1e7b3b8f0827a4cd53
artifacts/traces/v3_smoke_v3_smoke_002_M0_c6853660.jsonl
  95fa82d571993232f0b9c16ee2bed9959e61ab49fe7028f2f553685ee12a7323
artifacts/prompt_cache/v3_smoke_v3_smoke_002_c6853660.jsonl
  d130b6595632a53bb2e118553bbf9c7a498092a92162ef4ac3e31db40d975b0a
artifacts/embedding_cache/v3_smoke_v3_smoke_002_c6853660.jsonl
  872a3ae8ec6b61799793db4cc873251ac34e75d0e68aa5941e566210b874c36f
```

## Decision and next action

V3 is not passed. Do not start V4, V5, V6, performance calibration, or a new
live attempt until this failure is resolved under the frozen protocol or an
explicit protocol deviation is approved. The next mainline action is offline
diagnosis of the structured-output failure and service-side configuration
evidence. Do not edit the Graphiti prompt/schema/decoding policy to make this
attempt pass, and do not use GPT-5.5 as a substitute.
