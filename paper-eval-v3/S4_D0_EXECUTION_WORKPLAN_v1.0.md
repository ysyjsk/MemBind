# S4 D0 Minimal Smoke and Qualification Workplan v1.0

Date: 2026-08-14

Parent protocol:
`../（主实验）MemBind_PAPER_EVALUATION_WORKPLAN_v3_SMALL_FIRST_FINAL.md`

Parent protocol SHA256:
`4b81c89b33d407f04fc20862a81eab6badba16d0d61d98331cbe188d1bb4f41e`

Native-v2 configuration freeze:
`artifacts/paper_eval/native/NATIVE_BASELINE_V2_FREEZE.json`

## 1. Scope

S4 answers one narrow question:

> Can a deterministic serial control replay the exact Native U0 model and
> embedding outputs without live fallback, preserve the same construction work
> and episode coverage, and produce an exactly equal canonical graph?

S4 does not evaluate MemBind, choose concurrency, estimate Native quality, tune
K, add a benchmark, or change the common Reader/Judge. It has exactly two
steps:

```text
one DEVELOPMENT_EXPOSED history smoke
 -> only if PASS
fixed four DEVELOPMENT_EXPOSED history qualification
```

No additional qualification histories may be added.

## 2. Method identities

### U0 capture

U0 remains the pinned upstream Native serial construction path. The only
capture adapters are transparent recording wrappers around its LLM and
embedding clients. U0 capture must not install deterministic candidate-order
stabilizers, alter Graphiti resolution/invalidation, change retrieval, or use
a prior cache.

### D0 replay

D0 uses the same serial `add_episode` path, source order, dataset projection,
Graphiti version, construction configuration, embedding namespace, and Neo4j
version. It adds only declared deterministic controls:

```text
prompt/response oracle       read-only
embedding oracle             read-only
edge-search stabilization    enabled
node-resolution stabilization enabled
edge-query stabilization     enabled
node-query stabilization     enabled
live model fallback          forbidden
```

D0 is a supporting correctness control, not the headline Native baseline and
not a paper performance treatment.

## 3. One-history smoke

Frozen history:

```text
history_id       07741c45
episode_count    49
data_role        DEVELOPMENT_EXPOSED
```

Frozen execution order:

```text
U0 model/embedding capture
 -> seal prompt and embedding caches
 -> D0 read-only replay in a fresh namespace
 -> compare coverage, work contract, canonical graph, and cache integrity
```

Capture and replay use distinct fresh `pev3-s4-*` namespaces. Every namespace
must be empty before first mutation and is cleaned by exact `group_id` only
after its graph export. Global Neo4j deletion is forbidden. Existing S1/S2
namespaces are read-only and must remain untouched.

Because Graphiti stores the isolation `group_id` inside Entity records, the
offline comparison projects only that field to the fixed placeholder
`__S4_ISOLATED_NAMESPACE__` in both exported artifacts before hashing. This is
an artifact-only namespace alpha-renaming; it does not modify Neo4j data or
normalize any entity, edge, temporal, episode, or attribute content.

The runner writes an append-only event after every intent/publication/failure,
atomically replaces a checkpoint after every published episode, and resumes
only a contiguous durable prefix. Console output is line-buffered and
informative, but JSONL/checkpoint artifacts are authoritative.

## 4. Smoke hard gates

All must pass:

```text
capture episode coverage             49/49 exactly once, source ordered
replay episode coverage              49/49 exactly once, source ordered
capture LLM and embedding calls      > 0
replay live LLM calls                0
replay live embedding calls          0
unexpected prompt/embedding misses   0
live fallback                        0
cross-encoder calls outside oracle   0
prompt/embedding cache mutation      0 during replay
same resolved prompt count           capture == replay
same resolved embedding count        capture == replay
canonical graph parity               100% exact
namespace loss/duplicate             0 / 0
cleanup scope                         exact group_id only
```

Any graph difference is reported per canonical component and classified before
continuation. A threshold such as F1>=0.95 cannot replace exact smoke parity.

## 5. Runtime preflight and authority

The S3 freeze disclosed a construction revision evidence conflict. Therefore,
before any S4 live mutation, one bounded read-only preflight must verify:

```text
construction model       qwen3-32b-fp8
vLLM version             0.26.0
max_model_len            >= 65536
embedding model          qwen3-embedding-0.6b
Neo4j                    connectivity PASS
capture/replay namespaces empty
S1 historical namespace unchanged
```

If either model service is unreachable or mismatched, STOP and report. Do not
rewrite the contract, change completion limits, switch model, or probe another
endpoint. A successful preflight still does not itself authorize mutation; a
single-use S4 smoke authority must bind its evidence and exact run IDs first.

## 6. Four-history qualification

Only after the one-history smoke is sealed PASS, use the existing four
DEVELOPMENT_EXPOSED calibration histories. Do not add fresh or held-out data.

Hard gates remain zero miss/fallback, 100% coverage, preserved LLM call-count
contract, no hidden semantic fallback, and no unexplained canonical drift. The
project-specific token/work ratio `[0.95, 1.05]` is reported as a MemBind
fairness guardrail, not a field standard. Retrieval and QA are paired
descriptives using the S3 common evaluation policy; no `D0 >= U0 - 1pp` rule is
introduced.

## 7. TDD and stop rules

Every surface follows:

```text
failing offline test
 -> minimal implementation
 -> focused GREEN
 -> full paper-eval-v3 offline GREEN
 -> sealed evidence/authority
 -> live action, if authorized
```

STOP immediately on service disconnect, namespace contamination, cache miss,
fallback, lost/duplicate episode, non-contiguous resume, cache mutation,
cross-encoder call, or unexplained graph drift. A stopped run keeps its durable
prefix and never becomes mergeable.

## 8. Next-stage boundary

S4 PASS may freeze D0 and authorize only S5 offline design. It does not
authorize A0/P*/M* live work, a concurrency sweep, PILOT, or paper-scale runs.
