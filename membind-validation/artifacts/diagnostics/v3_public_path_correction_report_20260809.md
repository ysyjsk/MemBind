# V3 public-path reproduction and evidence-closure report

Generated: 2026-08-09

## Outcome

The frozen V3 experiment remains blocked at M0 source sequence 1 by
`v3_smoke_002_m0_structured_output_failure`. The correct public Graphiti client
path reproduces the original structured-output truncation exactly. This is a
real V3 failure result, not an M0/M2 semantic-parity result: M2 did not start,
and M1 remained forbidden by the protocol.

The result does not authorize `v3_smoke_003`, V4, V5, or V6. It also does not
authorize changes to the Graphiti prompt, schema, model, decoding parameters,
completion budgets, or retry policy.

## Why the first compatibility probes were invalid

The first source-0 and source-1 probes called the private `_generate_response`
method directly. The real Graphiti integration calls public `generate_response`,
whose wrapper appends a 227-character language instruction to the system
message. Omitting that wrapper reduced both observed prompt counts by 43 tokens:

| request | historical | private-path probe | delta |
|---|---:|---:|---:|
| source 0 | 4515 | 4472 | -43 |
| source 1 | 5795 | 5752 | -43 |

The private-path probes parsed, but they did not send the historical public
request. Therefore their apparent construction-runtime drift and the derived
artifact below are invalidated diagnostics:

```text
artifacts/diagnostics/v3_construction_runtime_drift_20260809.json
SHA256 c4d89fd9f3b827f205a9127c14f64be425723ff240dca92d6fdf8ff52c8d0cb8
```

The two private-path probe artifacts remain immutable history. Their internal
`frozen_actual_schema_request_parsed` labels are non-authoritative for V3.

## Correct public-path reproduction

The corrected probe reconstructs source sequence 1 and calls public
`generate_response` through the same `OpenAIGenericClient` path as Graphiti. It
does not call Neo4j or the embedding endpoint. Across four high-level attempts,
the public path made eight completion calls using the frozen retry sequence
`2048 -> 8192` each time.

| completion budget | calls | prompt tokens | finish reason | body SHA256 |
|---:|---:|---:|---|---|
| 2048 | 4 | 5795 | `length` | `d9340f0bc347bbb5d7049aa55bee2739485755444f28e216e523c4bd3a5b0a16` |
| 8192 | 4 | 5795 | `length` | `94fb64c3921b3e1e7bfecee99e6faa00e620fea7f949cc0d839d8b185035aef0` |

Both hashes are byte-for-byte identical to the corresponding bodies retained
from `v3_smoke_002`. Response bodies were used locally for comparison but were
not copied into the compatibility artifact. No secret or Authorization header
was persisted.

The raw public-path artifact is retained unchanged:

```text
artifacts/environment/v3_actual_schema_compatibility_probe_20260809_003_public_path.json
SHA256 7df5ada3d29142eb82190d825938d546cdb7016f95538e3fb01e32ca0ed0ca03
```

Its observed events are authoritative, but its derived classification is not:
the first classifier assumed one retry pair and hardcoded the outer retry
count. The corrected classification was derived without another model call:

```text
artifacts/environment/v3_actual_schema_compatibility_probe_20260809_004_reclassified.json
SHA256 d3caf163af7639f2dcbc5322d4f1e3e5a3d23067f2638bb4398d15c4c2b9bcfb
classification: exact_historical_truncation_reproduced
model_called_during_correction: false
```

`004_reclassified` is the authoritative compatibility artifact.

## Schema and service evidence

Offline reconstruction shows that the installed Graphiti v0.29.3 extraction
schema has one unbounded array,
`ExtractedEntities.extracted_entities`. It has no finite `maxItems` or semantic
stop condition, and no `uniqueItems` constraint. Repeated schema-valid entity objects can therefore remain a
grammar-compatible prefix until the token budget is exhausted. This explains
the shape of the retained output, but it does not establish what caused the
repetition.

The vLLM 0.26.0 source contract confirms that
`response_format.type=json_schema` is normalized into structured outputs and
that the default backend selection is `auto`. It does not reveal which backend
the deployed process selected.

The earlier metadata attempt02 followed an external proxy route and is invalid
as construction-service evidence. Direct metadata attempt03 is authoritative:

```text
artifacts/environment/v3_vllm_metadata_probe_20260809_attempt03.json
SHA256 8f734c49f065f269fdbda22a721d67c93dec580e4e4210872a6be8e71249992b
```

Metadata attempt03 proves a direct `NO_PROXY` path, vLLM 0.26.0, served model
`qwen3-32b-fp8`, model root
`/home/lhx/liuyi/models/Qwen3-32B-FP8`, maximum context 40960, and healthy
version/models/health endpoints. `/server_info?config_format=json` returns 404,
so the selected structured-output backend and startup configuration remain
unobserved from this machine.

The retained official Hugging Face manifest is expected-checkpoint provenance,
not proof that the deployed checkpoint/tokenizer/chat template is identical.

## Supported and unsupported conclusions

Supported:

```text
For the exact frozen public Graphiti request, Qwen/vLLM deterministically
returned a schema-compatible JSON prefix that exhausted both permitted
completion budgets in every retry.
```

Not supported:

- that the client JSON parser is defective;
- that embedding or Neo4j caused this first failure;
- that M0 and M2 differ semantically;
- that the model alone caused the repetition;
- that a particular vLLM structured-output backend was selected or misconfigured;
- that raising token budgets or changing the schema is an approved remedy.

The remaining alternatives are model behavior, the selected vLLM
structured-output backend/configuration, or another request/runtime interaction.
Current evidence cannot distinguish them.

## Current gate

The immediate action scope is `evidence_collection_only`. Obtain and persist a
sanitized construction vLLM process argv/startup log, or equivalent evidence
that identifies the selected structured-output backend and relevant config.
SSH from this machine was denied and `/server_info` is unavailable, so that
evidence must come from the construction-service host or its operator logs.
If the configured backend is `auto`, request-level backend-selection log
evidence (or an equivalent request ID trace) is also needed because argv alone
may not reveal which backend handled this request.

Only after reviewing that evidence may the next decision be made:

- if it proves a deployment/configuration defect that can be corrected without
  changing the frozen experiment contract, validate that service-side correction
  with the same public-path probe;
- otherwise stop and request explicit protocol-deviation approval.

Until then, `v3_smoke_003` and all V4/V5/V6 work remain forbidden.

## TDD record

The closure contract was introduced as a failing test before these state and
documentation changes:

```text
artifacts/tdd/v3_evidence_closure_contract_red_128.log
SHA256 ee1de38b4660618c56d4ba52f8cfdf1a0b676fb80daee7a99ddf5a68b02faa72
```

The report intentionally does not self-reference a final green-log hash.
Machine-readable green and full-regression evidence is recorded in
`CURRENT_STATE.json` after each test run completes.
