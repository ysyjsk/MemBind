# S2 Post-R0 Offline Design Draft

Status: non-authoritative engineering draft. This file is not a protocol,
authorization, stage transition, retrieval-policy decision, or paper result.

The draft exists only to prepare offline TDD work while the authorized S2-R0
probe waits for local Neo4j. It must not be added retroactively to the current
S2-R0 authorization binding set.

## 1. Fixed boundary

Every successful S2-R0 branch must preserve:

```text
retrieval_policy_selected = false
s3_authorized = false
whole_graph_quality_conclusion = NOT_INFERRED
```

S2-R0 is a bounded Episode BM25/RRF diagnostic. It does not satisfy the parent
protocol's S2 requirement for a frozen Native retrieval policy, Reader/Judge
identity, Evidence Recall@10, and QA Accuracy. It therefore cannot directly
produce `NATIVE_BASELINE_FREEZE.json`.

No design decision below may depend on the observed S2-R0 score. In particular,
the probe result cannot be used to search retrieval configurations or choose
the best-performing Graphiti surface on the exposed development history.

## 2. Result decision table

| Sealed outcome | Permitted interpretation | Immediate action | Required next authority |
| --- | --- | --- | --- |
| `EPISODE_SURFACE_RECALL_ALL` | Both gold sessions are reachable on the tested Episode surface | Seal and stop | Separate, preregistered S2-completion protocol |
| `PARTIAL_EPISODE_SURFACE_REACHABILITY` | At least one but not all gold sessions are reachable | Seal and perform offline diagnosis only | New protocol and one-shot authority for any further live probe |
| `EDGE_AND_EPISODE_SURFACES_NEAR_ZERO` | The tested Edge and Episode surfaces are near-zero | Seal; Node, Community, and multi-surface remain untested | New protocol after offline root-cause analysis |
| Corpus/config/source/authorization failure | No retrieval conclusion | Preserve sanitized failure and stop | New run ID and new authority after RED/GREEN repair |
| Runtime/infrastructure failure | No scientific retrieval conclusion | Preserve sanitized failure and stop | Explicit infrastructure-failure review and new authority |

Automatic retry, cleanup, namespace rebuild, extra search, Reader/Judge calls,
or S3 transition are forbidden in every row.

## 3. Proposed offline modules

These names are provisional. Implement them only after a post-R0 workplan is
approved.

### `s2_r0_result_verifier.py`

Pure, read-only verification of the sealed result chain:

```text
offline qualification
  -> one-shot authorization
  -> exclusive consumption
  -> result or sanitized failure
```

Proposed output:

```python
VerifiedS2R0Outcome(
    run_id: str,
    terminal_status: str,
    interpretation: str,
    authorization_sha256: str,
    consumption_sha256: str,
    graphiti_search_calls: int,
    neo4j_read_requests: int,
    forbidden_call_counts: dict[str, int],
    retrieval_policy_selected: bool,
    s3_authorized: bool,
)
```

The verifier must reject envelope, payload, run ID, namespace, corpus identity,
source binding, counter, or hash-chain drift. It must never read credentials,
query Neo4j, or deserialize raw question/session content into an artifact.

### `s2_completion_contract.py`

Pure schema and validator for a future S2-completion protocol. It must require
the retrieval policy to be selected from architecture and benchmark semantics
before numeric execution, rather than selected from R0 scores.

Minimum identity fields:

```text
retrieval surface and result unit
candidate generation and fusion/reranking recipe
top-k unit and metric definition
question-date/temporal-filter policy
Graphiti source and config hashes
Reader upstream source, prompt, model, and request contract
Judge rubric, parser, model, and qualification identity
dataset/split/role identities
retry/failure policy
redaction policy
```

The formal metrics remain separate:

```text
Evidence Recall@10
QA Accuracy
graph-sensitive construction correctness
```

No single metric may substitute for another.

### `s3_freeze.py`

Pure builder and validator for a future `NATIVE_BASELINE_FREEZE.json`. The
builder must refuse to create an artifact unless a separate S2 PASS artifact
and explicit S3 authorization are present and hash-valid.

Required bindings:

```text
Graphiti version/commit and critical source hashes
construction model and execution envelope
embedding model, fingerprint, dtype, pooling, normalization, instruction policy
Neo4j version/deployment
vLLM version and structured-output identity
prompt/schema/retry/cache identities
retrieval/Reader/Judge identities
instrumentation identity and measured overhead
reference-alignment status and numeric sanity
all upstream qualification and source hashes
```

The finalizer must use exclusive creation. Existing freeze artifacts are never
overwritten, and a later method result can never modify the Native baseline.

### `s4_d0_contract.py`

Pure synthetic-fixture contracts for the later D0 lane:

```text
1-history before 4-history
zero oracle miss/fallback
100% episode/source coverage
same call-count and work contract
100% canonical graph parity when only non-semantic nondeterminism is removed
explicit mismatch classification
token/work ratio in the preregistered [0.95, 1.05] project guardrail
paired descriptive retrieval and QA outputs
```

No capture, replay, construction, database, or model request belongs in this
offline module.

## 4. TDD order for the next approved workplan

### Gate A: sealed R0 verification

Write RED tests for:

1. Valid qualification-to-result hash chain.
2. Valid qualification-to-failure hash chain.
3. Authorization/consumption/result run-ID mismatch rejection.
4. Payload-seal and source-binding drift rejection.
5. Exactly one Graphiti search and positive Neo4j read count.
6. Zero construction LLM, embedding, cross-encoder, Reader, Judge, mutation,
   cleanup, and retry counters.
7. All three successful diagnostic interpretations.
8. Hard assertion that every interpretation leaves S3 unauthorized.
9. No raw question, episode, prompt, answer, endpoint, or credential in output.

Then implement the minimum pure verifier and run focused plus full offline GREEN.

### Gate B: outcome-independent S2 completion contract

Write RED tests for:

1. Missing or ambiguous result-unit rejection.
2. Metric/top-k-unit mismatch rejection.
3. Retrieval selection evidence that mentions R0 score rejection.
4. Reader prompt/source/model identity drift rejection.
5. Judge rubric/parser/qualification drift rejection.
6. Data-role overlap rejection.
7. Unfrozen retry or failure policy rejection.
8. Explicit separation of retrieval, QA, and graph-correctness metrics.

No production transport or live client is added in this gate.

### Gate C: S3 freeze schema

Write RED tests for:

1. S1 PASS plus full S2 PASS required.
2. S2-R0 diagnostic alone rejected.
3. Explicit one-shot S3 authority required.
4. Every runtime/source/config binding required.
5. Missing Reader or Judge identity rejected.
6. Existing freeze artifact never overwritten.
7. Payload SHA256 and file SHA256 independently verifiable.
8. Later method-result fields rejected from the Native freeze.

Implement only the pure builder/validator. Do not generate the production
freeze until a later approved stage transition.

### Gate D: D0 synthetic contracts

Write RED tests using only mocks and synthetic fixtures. Production adapters,
captures, and live runners remain out of scope until S3 is validly frozen.

## 5. Checkpoint and tmux design for later live stages

Long-running stages should use a repository-owned tmux launcher that records:

```text
session name
run ID
sealed authorization hash
checkpoint path
event-log path
stdout/stderr log path
terminal exit code
```

The launcher must consume authority before live I/O, write episode/block
checkpoints durably, and resume only when the relevant protocol explicitly
permits resume. Infrastructure failures and treatment-induced failures remain
distinct. A tmux restart must never silently convert a new attempt into a
resume or reuse a one-shot authorization.

## 6. Current non-actions

This draft does not authorize or perform:

```text
an additional S2-R0 call
retrieval-policy search or selection
Reader/Judge/model/embedding calls
Neo4j queries, cleanup, or rebuild
S3 freeze generation
S3/S4 stage-pointer updates
D0 capture/replay or construction
```

The only current live authority remains the already sealed, unconsumed
`s2r0-20260814-001` action.
