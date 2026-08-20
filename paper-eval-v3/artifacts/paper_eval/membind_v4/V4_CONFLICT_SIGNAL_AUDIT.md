# MemBind v4 Conflict-Signal Audit

## Status and Scope

Status: `COMPLETE`

This audit is pinned to Graphiti `0.29.3`, the frozen v3.1 State-Cut
certification, and the existing factorized NodeResolve path. It does not
change the U0/A0/P(C=2)/v3.1 results, arrival trace, workload, prompt, schema,
model, NodeResolve semantics, global `K=2`, or any persistent-write path.

Conflict prediction controls performance admission only:

> Predict for performance. Validate for correctness.

No conflict classification authorizes reuse. Every launched candidate must
still be rematerialized on its exact predecessor state and pass complete
semantic-call and effect-context identity validation. A mismatch is a MISS,
the speculative response is discarded, and Native exact NodeResolve runs.

## Audited NodeResolve Path

The production path is:

1. v3.1 `prepare()` extracts nodes and edges from the source and its frozen
   evidence fence without reading persistent state.
2. `PreparedArtifact` stores canonical `raw_nodes`, `raw_edges`, and pure
   intermediates.
3. NodeResolve materialization against an explicitly selected legal state:
   - materializes the episode;
   - routes the group namespace;
   - retrieves latest previous episodes;
   - materializes extracted nodes;
   - embeds extracted names;
   - retrieves ordered existing-node candidates from the same group;
   - applies deterministic exact/fuzzy resolution;
   - captures, but does not execute, an unresolved NodeResolve LLM request.
4. Provider execution occurs only after admission.
5. Once the exact predecessor is ready, the request and effect context are
   rematerialized against that exact state.
6. Reuse is allowed only after full identity equality. Edge resolution,
   attributes, summary work, ordered publication, and all writes remain in
   the exact Native continuation.

## SAFE_PRE_SPEC_SIGNALS

### PreparedArtifact-only signals

The following fields are already known without a graph read or NodeResolve
LLM execution:

- `source_sequence` and immutable source/artifact identities;
- `group_id`, used as the namespace boundary;
- extracted entity `name`;
- Graphiti-compatible exact-normalized entity name;
- extracted entity labels/type projection;
- relation source/target references, only after resolving extraction UUIDs
  to nodes within the same PreparedArtifact;
- previously completed, causally prior runtime telemetry.

The canonical direct-overlap key is:

```text
(group_id, graphiti_exact_normalized_name)
```

Graphiti `0.29.3` exact normalization is reproduced exactly:

```text
lowercase
-> collapse one or more whitespace characters to one ASCII space
-> trim leading and trailing whitespace
```

Labels are retained as a sorted diagnostic projection. They are deliberately
not part of direct-overlap equality: equal canonical names with different
extracted labels may still resolve to the same persistent entity.

Extracted node UUIDs are generated during each extraction. They are safe only
for resolving relation endpoints inside that one artifact. They are not a
persistent identity and must never be compared across episodes.

### Current-published-state read-only signals

The following are legal only when an already materialized `SemanticCall` was
constructed against the explicitly identified current published state `M_f`:

- ordered candidate rows for each extracted entity;
- persistent existing-candidate UUIDs;
- deterministic-resolution state;
- stale-state execution mode (`LLM` or `NO_LLM`);
- stale-state previous-episode projection;
- stale-state rendered-request identity.

For conflict classification, stable existing identity is:

```text
SemanticCall.candidate_bindings[].uuid
```

The integer/string `candidate_id` and `candidate_order` values are local to a
single materialization. They may participate in full version validation, but
they are not stable cross-call conflict identities.

The state-bound enrichment API accepts an already materialized
`SemanticCall`. It receives no graph driver, retrieval callback, embedder, or
provider capability, so enrichment itself cannot cause a state read or LLM
call.

### Frontier potential effects

Before the frontier NodeResolve response is known, conservative potential
effects are:

- frontier PreparedArtifact canonical entity keys;
- frontier materialized existing-candidate UUIDs;
- frontier namespace.

After exact validation and ordered publication, bounded runtime telemetry may
record:

- published canonical entity keys;
- exact published/touched entity UUIDs when exposed by the Native result;
- prior HIT/MISS/no-speculation outcomes.

Only completed, causally prior events enter the hot-entity window. The current
candidate outcome and future trace labels are unavailable to admission.

## Field Classification

| Candidate field | Classification | Permitted use |
| --- | --- | --- |
| extracted canonical/name representation | `SAFE_PRE_SPEC_SIGNALS` | Primary direct-overlap key |
| entity type/labels | `SAFE_PRE_SPEC_SIGNALS` | Diagnostics/completeness, not required for equality |
| group/namespace | `SAFE_PRE_SPEC_SIGNALS` | Mandatory isolation key |
| extracted node UUID | Safe only within one artifact | Relation endpoint-to-name mapping |
| existing candidate UUID on legal `M_f` | `SAFE_PRE_SPEC_SIGNALS` after read-only materialization | Set-overlap signal |
| local `candidate_id` | `NOT_ALLOWED` as stable identity | Per-call ordinal only |
| candidate ordering on `M_f` | State-bound diagnostic | Exact identity, not conflict equality |
| relation endpoints | Safe when frozen and locally resolvable | Coverage/completeness signal |
| previous episodes from `M_f` | State-bound diagnostic | Exact identity, not artifact-only signal |
| exact predecessor previous episodes | `UNAVAILABLE_BEFORE_EXACT_STATE` | Exact validation only |
| previously published/touched entity IDs | `SAFE_PRE_SPEC_SIGNALS` | Bounded causal hot telemetry |
| current frontier final touched IDs | `UNAVAILABLE_BEFORE_EXACT_STATE` | Cannot infer from a prediction |
| exact predecessor candidate set/order | `UNAVAILABLE_BEFORE_EXACT_STATE` | Exact rematerialization only |
| exact rendered prompt/token identity | `UNAVAILABLE_BEFORE_EXACT_STATE` | Exact validation only |
| exact execution mode | `UNAVAILABLE_BEFORE_EXACT_STATE` | Exact validation only |
| final UUID map / duplicate selection | `UNAVAILABLE_BEFORE_EXACT_STATE` | Resolution output only |
| EdgeResolve, Attributes, Summary effects | Outside v4 scope | Native exact continuation only |

## Minimal ConflictSignature

The implemented signature contains:

- source sequence;
- verified single namespace;
- sorted unique exact-normalized entity names;
- sorted entity-type projection per canonical name;
- locally resolved relation endpoint names;
- optional state-bound existing candidate UUIDs;
- published-state version for optional state-bound enrichment;
- completeness flag and stable incomplete reason codes;
- PreparedArtifact SHA-256.

The public projection emits only hashes, counts, versions, and reason codes.
It never persists entity names, raw prompt/response content, token IDs,
embeddings, source bodies, or secrets.

Malformed names, missing/mixed namespaces, duplicate extraction UUIDs,
unresolved relation endpoints, unavailable edge extraction, a bad artifact
seal, or a source mismatch fail closed. Incomplete primary evidence produces
`UNKNOWN`, never `LOW_CONFLICT`.

## Deterministic Classifier

Classification order is fixed:

1. Incomplete or invalid primary signal: `UNKNOWN`.
2. Verified namespaces differ: `LOW_CONFLICT` (`NAMESPACE_ISOLATED`).
3. Canonical entity-name sets intersect: `HIGH_CONFLICT`
   (`DIRECT_ENTITY_OVERLAP`).
4. Both legal state-bound existing UUID sets are available and intersect:
   `HIGH_CONFLICT` (`EXISTING_CANDIDATE_ID_OVERLAP`).
5. Candidate entity key or available UUID intersects the bounded prior hot
   window: `HIGH_CONFLICT` (`RECENT_HOT_ENTITY`).
6. Complete verified signals are disjoint: `LOW_CONFLICT`
   (`KNOWN_DISJOINT`).
7. Otherwise: `UNKNOWN`.

Missing optional existing-UUID enrichment alone does not invalidate a
complete PreparedArtifact-only signature. Missing or invalid primary entity
identity does.

`HIGH_CONFLICT` and `UNKNOWN` are never admitted for speculative transport.

## UNAVAILABLE_BEFORE_EXACT_STATE

Until exact predecessor `M_(i-1)` is legally published, the runtime cannot
know:

- its exact existing-candidate UUID set and ordering;
- exact latest previous episodes and their ordering;
- exact deterministic-resolution branch;
- exact `LLM` versus `NO_LLM` execution mode;
- exact rendered request and token sequence;
- exact NodeResolve response;
- exact resolved UUID map and duplicate pairs;
- exact final touched/written entity UUIDs;
- exact effect context;
- downstream edge, attribute, summary, commit, or publication results.

A stale-state value for any item above may inform profitability. It cannot
substitute for the exact version during reuse validation.

## NOT_ALLOWED

- Reading `M_(i-1)` or any future/unpublished state during admission.
- Executing the NodeResolve LLM merely to classify conflict.
- Querying a broader or different namespace.
- Treating extracted UUIDs as persistent entity IDs.
- Comparing local `candidate_id` values across materializations.
- Fuzzy, semantic, prefix, or partial-hash validation.
- Treating conflict classification as proof of semantic independence.
- Skipping exact rematerialization or complete identity validation.
- Speculative EdgeResolve, Attributes, Summary, commit, or persistent writes.
- Using current/future HIT-MISS labels during live classification.
- Retrospective episode selection or threshold adjustment after outcomes.

## Resource and Correctness Boundary

The production residual predicate remains exactly:

```text
configured K == 2
active_count == 1
active_frontier_count == 1
waiting_frontier_count == 0
frontier bind region active
frontier transport permit active
active speculation == 0
```

`waiting_compile_count` is telemetry only. It is intentionally not an
admission predicate, so compile backlog cannot structurally starve c01_ca.
A snapshot proves policy admission, not atomic transport reservation; actual
transport overlap remains a separately recorded fact.

The exact semantic fingerprint includes rendered request/token identity,
extracted-node projections, candidate ordering/bindings, previous episodes,
episode context, entity types, model, decoding, response schema, execution
mode, and operator revision. Effect-context identity remains independently
required where present.

A LOW prediction can therefore be wrong without violating correctness:

```text
LOW prediction
-> speculative stale call
-> exact predecessor rematerialization
-> full identity mismatch
-> MISS
-> discard stale response
-> execute exact Native NodeResolve
```

## Code Evidence

- `src/paper_eval/membind_v31/prepared_artifact.py`
- `src/paper_eval/membind_v31/graphiti_adapter.py`
- `src/paper_eval/membind_v4/graphiti_factorization.py`
- `src/paper_eval/membind_v4/semantic_call.py`
- `src/paper_eval/membind_v4/node_resolve_adapter.py`
- `src/paper_eval/membind_v4/live_adapter.py`
- `src/paper_eval/membind_v4/speculative_adapter.py`
- `src/paper_eval/membind_v4/conflict_signature.py`
- `src/paper_eval/membind_v4/conflict_classifier.py`
- Graphiti `0.29.3` `utils/maintenance/dedup_helpers.py`
- Graphiti `0.29.3` `utils/maintenance/node_operations.py`
- Graphiti `0.29.3` `search/search_utils.py`

## Audit Conclusion

The legal minimum signal is sufficient for a deterministic, conservative
profitability classifier. It is not sufficient to prove semantic
independence. Correctness remains exclusively version-bound and exact.
