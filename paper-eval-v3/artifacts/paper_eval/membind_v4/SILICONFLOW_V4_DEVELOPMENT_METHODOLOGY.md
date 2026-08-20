# MemBind v4 SiliconFlow Development Methodology

Date: 2026-08-20

This document defines a development-only provider compatibility lane. It does
not amend the frozen v3.1 envelope, the A1 amendment, the original c01 STOP,
or any formal main-table comparator.

## Purpose

Use SiliconFlow to validate the parts of v4 that are independent of the local
vLLM scheduler:

- authenticated OpenAI-compatible model discovery;
- the exact Qwen 32B structured-request shape;
- the Qwen 0.6B embedding response shape;
- removal of local-vLLM-only request fields;
- content-safe request/response telemetry;
- semantic-call validation, speculative write fencing, and ordered-publication
  unit/integration behavior.

The hosted provider is not used to estimate a GPU result. Its network path,
queue, tokenizer/cache implementation, structured-output backend, and service
envelope differ from the target vLLM deployment.

## Provider Profile

```text
provider                 = SILICONFLOW_QWEN
base_url                 = https://api.siliconflow.cn/v1
construction_model       = Qwen/Qwen3-32B
embedding_model          = Qwen/Qwen3-Embedding-0.6B
embedding_dimension      = 1024
temperature              = 0.0
structured_output        = json_schema requested; response validated
provider_backend         = provider-managed/unknown
cache_salt_sent          = false
formal_main_table        = ineligible
```

The API key is supplied only through `SILICONFLOW_API_KEY` in the process
environment. It is never written to a manifest, trace, log, exception, or
artifact. The key pasted in the task should be revoked or rotated before a
long-running experiment.

## Bounded Live Probe

The single bounded probe performs exactly three requests:

1. `GET /models`, authenticated, requiring both exact case-sensitive model IDs.
2. `POST /embeddings`, authenticated, requiring one vector of dimension 1024.
3. `POST /chat/completions`, with `temperature=0`, a strict JSON Schema request,
   `max_tokens=128`, and `enable_thinking=false`.

Run it from `paper-eval-v3`:

```bash
SILICONFLOW_API_KEY='<process-only-key>' \
PYTHONPATH=src:.:../membind-validation/.venv/lib/python3.12/site-packages \
.venv/bin/python scripts/run_membind_v4_siliconflow_probe.py \
  --output-root artifacts/paper_eval/membind_v4/siliconflow_probe-<run-id>
```

The command must produce:

```text
SILICONFLOW_PROBE.json
status = PASS
development_only = true
formal_main_table_eligible = false
mutations_performed = false
credentials_recorded = false
```

The probe artifact contains only model IDs, dimensions, usage counts, and
SHA-256 projections of content. It does not create a Neo4j namespace or call
the v4 candidate runner.

## Optional Graphiti Compatibility Smoke

After the protocol probe passes, one isolated Graphiti episode may be used to
verify the actual Graphiti 0.29.3 client, embedding, and Neo4j wiring:

```bash
SILICONFLOW_API_KEY='<process-only-key>' \
PYTHONPATH=src:.:../membind-validation/.venv/lib/python3.12/site-packages \
.venv/bin/python scripts/run_membind_v4_siliconflow_graphiti_episode_probe.py \
  --output-root artifacts/paper_eval/membind_v4/siliconflow_probe-<run-id> \
  --group-id membind-v4-sf-compat-<run-id>
```

This smoke uses exactly one fresh Neo4j group and records public node,
relationship, and episode counts. It is a real mutation in that one test
group, but it is still development-only and is never merged into a formal
construction result. The successful 2026-08-20 smoke produced 1 episode, 2
nodes, and 1 relationship; no raw episode text or credential was persisted.

## Request Boundary

The SiliconFlow adapter normalizes the local request as follows:

```text
extra_body.chat_template_kwargs.enable_thinking -> enable_thinking=false
extra_body.cache_salt                       -> removed
```

The normal OpenAI `response_format.type=json_schema` payload remains intact.
No vLLM-specific `xgrammar` claim is made for SiliconFlow. A successful JSON
parse proves response validity only; it does not prove the same constrained
decoder or backend behavior as local vLLM.

## v4 Development Gates

The following gates remain meaningful in the hosted lane:

```text
semantic-call fingerprint equality / mismatch
exact validation completed
HIT/MISS bookkeeping
speculative persistent writes = 0
wrong-version reuse = 0
ordered publication
complete publication coverage
provider request/response trace alignment
```

The following values are diagnostic only and must not be compared with the
local-vLLM main table:

```text
makespan and freshness ratios
frontier service interference
useful-token throughput
hidden critical-path time as a GPU gain
prefix-cache hit or cache-affinity gain
GPU/KV-cache utilization and scheduler overlap
```

The pinned local Qwen tokenizer may be used to build a content-safe request
fingerprint. SiliconFlow does not expose its tokenizer or prefix-cache state,
so that fingerprint is a local request projection, not evidence of a hosted
provider cache hit.

## TDD Completion Gate

Before a hosted Graphiti compatibility run is considered, these tests must be
green:

```bash
PYTHONPATH=src:.:../membind-validation/src \
.venv/bin/python -m pytest -q \
  tests/test_membind_v4_siliconflow_probe.py \
  tests/test_membind_v4_semantic_call.py \
  tests/test_membind_v4_live_adapter.py \
  tests/test_membind_v4_live_block.py \
  tests/test_membind_v4_runner.py
```

The provider lane must additionally fail closed on model alias drift, missing
authentication, embedding dimension drift, malformed JSON, schema mismatch,
timeout, hidden retries, and any attempt to pass a SiliconFlow envelope to the
formal A1 reducer.

## Migration To Target vLLM

After the target services are available, discard the SiliconFlow provider
overlay and use the already sealed local-vLLM environment. The formal sequence
is:

```text
1. authenticated/local read-only preflight: construction + embedding + Neo4j
2. verify the existing A1 audit and amendment without regeneration
3. run the one authorized c01/A1/20-source candidate in a fresh namespace
4. reduce immediately using V4_A1_DEVELOPMENT_REFERENCE.json
5. apply the preregistered FREEZE / TUNE_TO_C02 / STOP gate
6. only after FREEZE, run the frozen four-history vLLM experiment
```

The local vLLM identity remains:

```text
construction endpoint = http://10.87.5.247:8000/v1/
embedding endpoint    = http://10.87.5.247:8001/v1
model                 = qwen3-32b-fp8
embedding model       = qwen3-embedding-0.6b
structured backend    = xgrammar
```

SiliconFlow artifacts must stay under a separate provider-compatibility root
and must never be supplied as `--reference`, `--a1-audit`, or
`--a1-amendment` inputs to the formal runner. The original v3.1 and A1 sealed
artifacts remain unchanged.

## Definition Of Done For This Lane

This development lane is complete when:

```text
provider probe PASS
provider probe artifact contains no credential or raw content
focused v4 TDD suite PASS
formal vLLM A1 identity remains untouched
methodology migration command is fixed and reproducible
```

It is not a v4 performance result and cannot produce a FREEZE decision for the
local-vLLM main table.
