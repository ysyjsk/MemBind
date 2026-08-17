# MemBind-v1 Implementation and Aligned Development Main-Table Workplan

Status: ACTIVE, user-authorized on 2026-08-17

This is a new, isolated implementation and benchmark lane. It does not amend,
rewrite, reseal, or upgrade the historical development decision. It exists
because the user explicitly authorized implementation of the candidate after
the methodology document was sealed.

## 1. Immutable Inputs

The following artifacts are read-only historical inputs. Their payload hashes
must be checked before a new run is admitted and copied into the new run
manifest. No code in this lane may write below their artifact roots.

| Input | Payload SHA256 |
| --- | --- |
| `baseline_suite/.../THREE_BASELINE_RESULTS.json` | `7c087a2368724f2f8cfb0f8e17cd5d2f54684e51b3cfb9203a0f6dc04eff4ef0` |
| `graph_quality_overlay/.../GRAPH_QUALITY_RESULTS.json` | `15bd92d9f8393a3614d8764cdb71752e59f0e0668bc2f5ccb1746df8dad31953` |
| `development_report/.../REPORT.json` | `ba060bd48fb933319b522ef5196c003919b2a0c0d2a81c3eb9f00f4b264e9c62` |
| `methodology_finalization/.../METHODOLOGY_DECISION.json` | `50a76d29ff973b67465940af94d3bc9c3814db04bad2774b4ea834b78ed22f4d` |
| `methodology_finalization/.../FINAL_METHODOLOGY_ENVELOPE.json` | `fdce14ca14af82e1f393663bcf822a3153cecbe86c93375a231ab71bcdddec1f` |
| `../main methodology document` | `1daa14b633a814bb6674260b617f7ac92356b8b238cb6f8df52e6d0a7e65cb37` |

The historical conclusion remains `BLOCKED_QUALITY_PROTOCOL`,
`NO_METHOD_SELECTED`, and `NOT_AUTHORIZED` for the old authority envelope.
This workplan authorizes a separately named candidate attempt only.

## 2. Frozen Candidate Scope

`MemBind-v1` means **node-only Evidence-Bounded Semantic Late Binding**:

```text
immutable source record + EvidenceFence
  -> extract_nodes only, without mutable graph access
  -> durable PreparedNodeArtifact
  -> source-ordered latest-state bind
  -> native node resolution, edge extraction/resolution, attributes, commit
  -> durable publication acknowledgement
```

It does not include node-and-edge relocation, parallel binding, selective
repair, a generic DAG scheduler, a second backend, or a scheduler ablation.
The fixed first configuration is:

```text
compile concurrency C = 1
prepared lookahead W = 1
request-level construction admission K = 2
bind workers = 1
bind policy = frontier-first / anti-starvation
```

`C`, `W`, and `K` are recorded observations and resource controls, not a
novelty claim. A compiler has no Neo4j driver, Graphiti retrieval callable,
candidate searcher, or mutable graph-state object. Edge extraction remains in
bind.

## 3. Correctness and Recovery Contract

The implementation must satisfy the following before a live call is made:

1. The source inventory is immutable, hash-bound, contiguous, and complete.
2. Evidence selection exactly applies group, source, reference-time, and
   last-N rules, returns chronological order, and fails closed when an equal
   timestamp crosses the Native last-N boundary without an explicit capture.
3. A prepared artifact is canonical, hash-bound, exclusive, and durable.
4. Durable state is
   `INTENT_DURABLE -> PREPARE_RUNNING -> PREPARED_DURABLE -> BIND_RUNNING ->
   COMMIT_RETURNED -> PUBLICATION_DURABLE`.
5. A crash after `COMMIT_RETURNED` produces
   `AMBIGUOUS_COMMIT_POISONED`; it is never resumed in place.
6. Bind begins only for `published_frontier + 1`, reads state only after the
   prior durable acknowledgement, and holds one namespace-scoped writer lease.
7. Duplicate canonical runtime UUIDs with equal projection coalesce; conflicting
   projections fail closed.
8. Request admission records observed in-flight maxima and never exceeds K.

The new code lives only in `src/paper_eval/membind_v1/`, with matching
`test_membind_v1_*.py` tests and a dedicated runner. Historical `s5_*` code is
reference material only because its prepare path reads mutable Graphiti state.

## 4. Comparable Main-Table Contract

The sealed U0 and P(C=2) rows have different arrival timestamp semantics, so
their existing P95/P99 freshness values are historical diagnostics only. A
valid first comparison therefore has two surfaces:

1. **Frozen reference surface:** sealed U0/A0/P(C=2) aggregates, labelled
   `NOT_CROSS_METHOD_FRESHNESS_COMPARABLE`.
2. **Aligned development surface:** new fresh-namespace U0-aligned,
   P(C=2)-aligned, and MemBind-v1 rows using the same four-history source
   manifest, pre-generated open-loop arrival trace, admission K=2,
   model/embedding/Graphiti identities, warmup/cache/quiescence policy, and
   per-history checkpoints.

The aligned table records these predeclared columns:

| Method | QA | Session R@10 | Direct violations | P95 freshness | Goodput | Makespan |
| --- | --- | --- | --- | --- | --- | --- |
| U0-aligned | common quality only | common quality only | exact count | aligned trace | aligned trace | aligned trace |
| P(C=2)-aligned | common quality only | common quality only | exact count | aligned trace | aligned trace | aligned trace |
| MemBind-v1 node-only | common quality only | common quality only | must be zero | aligned trace | aligned trace | aligned trace |

The present graph-native QA overlay is degenerate (`0/4` for all methods).
Until a separately versioned common quality repair qualifies a nondegenerate
U0 denominator, the QA cell is rendered as `NQ: graph-native protocol
degenerate`; no quality-preservation claim is permitted. The first table is
development-only, uses history as the experimental unit, and treats pooled
episode quantiles as descriptive.

## 5. Test-Driven Execution Order

```text
RED pure source/fence/delta/frontier/admission tests
  -> minimal pure implementation
  -> focused GREEN
  -> RED durable store + failure/recovery tests
  -> GREEN durable implementation
  -> RED Graphiti node-only adapter/parity tests with fakes
  -> GREEN live adapter
  -> RED aligned plan/table provenance tests
  -> GREEN benchmark/table implementation
  -> related and full offline GREEN
  -> 3-5 episode fresh-namespace MemBind smoke
  -> one aligned 49-episode U0/P(C=2)/MemBind development history
  -> stop decision
  -> four-history aligned development table only when the prior gate passes
```

Long runs use `tmux`, append JSONL at each lifecycle transition, and checkpoint
after every durable publication. A model, transport, or Neo4j failure seals the
attempt non-mergeable and stops the chain; it does not mutate the source
manifest, retry policy, model envelope, historical artifacts, or namespace.

## 6. Artifact Roots

```text
artifacts/paper_eval/membind_v1/runs/<run-id>/
artifacts/paper_eval/aligned_main_table/runs/<run-id>/
```

Every result contains a source-manifest hash, arrival-trace hash, implementation
identity hash, historical-input bindings, result hash, and a terminal status.
