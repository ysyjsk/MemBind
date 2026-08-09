# V3 structured-output diagnosis

Generated: 2026-08-09

This is an offline, read-only diagnosis of the immutable `v3_smoke_002` M0
failure. It does not call a model, mutate Neo4j, or use the quarantined
GPT-5.5 lane.

## Proven facts

The first divergence is M0 `source_sequence=1`, during Graphiti entity
extraction. Source sequence 0 completed; source sequence 1 performed no
embedding and no database publication after entering `add_episode`. M2 never
started and M1 remained forbidden.

The retained failure artifact contains eight parse-failure records. They close
exactly as four outer Graphiti/tenacity attempts, each with the frozen budget
sequence `2048 -> 8192`:

| property | 2048 budget | 8192 budget |
|---|---:|---:|
| prompt tokens | 5795 | 5795 |
| completion tokens | 2048 | 8192 |
| finish reason | `length` | `length` |
| response length (characters) | 5386 | 21456 |
| distinct response hashes across four attempts | 1 | 1 |

The 2048-byte-string response is an exact prefix of the 8192 response in all
four pairs. The two response hashes are:

```text
2048: d9340f0bc347bbb5d7049aa55bee2739485755444f28e216e523c4bd3a5b0a16
8192: 94fb64c3921b3e1e7bfecee99e6faa00e620fea7f949cc0d839d8b185035aef0
```

Both responses end at EOF inside an incomplete JSON array. The larger budget
does not finish the object; it continues the same local entity pattern until
the larger budget is exhausted. The diagnostic JSON records the complete
hash/prefix analysis without persisting response bodies:

```text
artifacts/diagnostics/v3_smoke_002_structured_failure_diagnostic_20260809.json
SHA256 5629b37d3c2aa4da004d17c858c819a2adf67326c11abb1a21ab461d219344a2
```

The source failure artifact hash is:

```text
artifacts/llm_failures/v3_smoke_v3_smoke_002_M0_c6853660.json
SHA256 718e09c45f10744f2a1a7a7027a37df23566bf21716ace1e7b3b8f0827a4cd53
```

## Schema finding

The installed Graphiti v0.29.3 `ExtractedEntities` model, after the frozen
single-episode `episode_indices=[0]` constraint, has an unbounded
`extracted_entities` array. It specifies no `maxItems` or `uniqueItems`, and it
has no semantic stop condition. The constrained schema fingerprint is
`96cedd296936b90ddbed2156b20411b1662389f1c94c7a0b57df00f3cadd21d5`.

Therefore repeated, structurally valid entity objects are compatible with a
working grammar until the generation budget ends. This explains why the
observed output is a valid-prefix loop, but does **not** prove that the schema
caused the model loop or that the server selected the wrong guided-decoding
backend.

## Upstream vLLM contract evidence

The exact vLLM `v0.26.0` upstream sources were checked at commit
`568afb3a13806beb53bb2e6bd518269357b237c0`. The persisted contract is:

```text
artifacts/environment/vllm_0_26_structured_output_contract_20260809.json
SHA256 91e9adb97547e36c7113ba9e60e790b8703fed5b6fb96ac26f4b37f3552b3d83
```

That source contract says `response_format.type=json_schema` is normalized into
structured outputs; the server default backend is `auto`, with xgrammar and
guidance among the supported backends. It does not reveal which backend the
deployed process selected for this request.

## Current service-side evidence

Two no-generation metadata attempts were made after the offline diagnosis:

1. `attempt01` exposed an instrumentation bug: the probe passed timeout as a
   positional `urllib` argument. It is retained as a test failure artifact and
   is not service evidence.
2. `attempt02` used the corrected keyword timeout. `/version`, `/v1/models`,
   `/server_info?config_format=json`, and `/health` all timed out at 5 seconds;
   no completion endpoint was called.

```text
artifacts/environment/v3_vllm_metadata_probe_20260809_attempt02.json
SHA256 c78a495739533515c6d871897e5d287bf75dbf01c108b389a3a20f647b38f348
```

An existing SSH key was not available (`Permission denied (publickey,password)`),
so process argv and startup logs cannot be read from this machine. The clean
preflight immediately before `v3_smoke_002` remains authoritative for that
historical time; the later timeout only describes current reachability.

## Classification boundary

Supported classification:

```text
Qwen/vLLM returned deterministic, schema-compatible-prefix structured output
that exhausted both frozen completion budgets.
```

This is not classified as a client JSON parser defect, embedding-oracle miss,
M0/M2 semantic divergence, database cleanup failure, or GPT-related behavior.
The following alternatives remain unresolved:

- guided decoding was active with an unbounded schema and the model repeated;
- the deployed backend/configuration did not apply the intended schema to this
  request;
- another vLLM request/runtime interaction affected this complex schema.

## Required unblock evidence

Before any new live V3 attempt, obtain one of:

- sanitized construction vLLM process argv/startup-log evidence containing the
  structured-output backend/configuration; or
- sanitized `/server_info?config_format=json` output when the development
  endpoint is enabled; plus
- a frozen-protocol compatibility probe covering the actual extraction schema,
  with request ID/backend evidence if the service exposes it.

Do not change Graphiti prompts, schema, decoding policy, model, completion
budgets, or retry count merely to make the smoke pass. If the required evidence
shows that a contract change is necessary, stop and request explicit protocol
deviation approval.
