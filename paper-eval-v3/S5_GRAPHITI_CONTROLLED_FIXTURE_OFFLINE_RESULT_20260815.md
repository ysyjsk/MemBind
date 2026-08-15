# S5 Pinned Graphiti Controlled Fixture Offline Checkpoint

Date: 2026-08-15

## Scope

This checkpoint continues the frozen S5 production-method lane under
`paper-eval-v3/`. It is an offline qualification of the installed Graphiti
0.29.3 semantic path only. It does not start vLLM, an embedding service,
Neo4j, a namespace, an S5 live runner, or any authority-consuming action.

The fixture uses the parent environment:

```text
/data/predator/ly/MemBind/membind-validation/.venv/
graphiti-core 0.29.3
commit 021d3a57d511f21b10adaf7fa923bd5c1fce5e9d
```

## TDD Evidence

The new RED collection error is preserved in:

```text
logs/TDD_RED_S5_GRAPHITI_CONTROLLED_FIXTURE_20260815.xml
```

Focused GREEN evidence:

```text
logs/TDD_GREEN_S5_GRAPHITI_CONTROLLED_FIXTURE_20260815.xml
logs/TDD_GREEN_S5_GRAPHITI_REAL_EDGE_20260815.xml
logs/TDD_GREEN_S5_GRAPHITI_ALIAS_EDGE_20260815.xml
logs/TDD_GREEN_S5_GRAPHITI_TEMPORAL_INVALIDATION_20260815.xml
logs/TDD_GREEN_S5_GRAPHITI_RETRY_FAILCLOSED_20260815.xml
logs/TDD_GREEN_S5_GRAPHITI_PROVIDER_RESET_20260815.xml
logs/TDD_GREEN_S5_GRAPHITI_MULTI_SOURCE_20260815.xml
logs/TDD_GREEN_S5_GRAPHITI_RETRY_IDEMPOTENCE_WITNESS_20260815.xml
logs/TDD_GREEN_S5_GRAPHITI_RETRY_WITNESS_STRICT_20260815.xml
logs/TDD_FOCUSED_GREEN_S5_GRAPHITI_REAL_FIXTURE_20260815.xml
logs/TDD_FOCUSED_GREEN_S5_GRAPHITI_CONTROLLED_FIXTURE_20260815.xml
```

Focused controlled-fixture suite: `15 passed`. The current adapter/semantic/
artifact focused selection is `35 passed`; the added adapter contract checks
per-case reset, independent snapshot authority, and oracle-free callback input.

Full paper-eval-v3 offline regression:

```text
logs/TDD_FULL_OFFLINE_GREEN_S5_GRAPHITI_REAL_FIXTURE_20260815.xml
logs/TDD_FULL_OFFLINE_GREEN_S5_GRAPHITI_CONTROLLED_FINAL_20260815.xml
logs/TDD_FULL_OFFLINE_GREEN_S5_GRAPHITI_FINAL_20260815.xml
1103 passed, 0 failed, 0 errors, 0 skipped
```

The latest full-regression log is
`logs/TDD_FULL_OFFLINE_GREEN_S5_GRAPHITI_TYPED_PROVIDERS_20260815.xml`.
The latest focused typed-provider fixture log is
`logs/TDD_FOCUSED_GREEN_S5_GRAPHITI_TYPED_PROVIDERS_FINAL_20260815.xml`.

## Production-adapter typed-provider integration checkpoint

The next RED test intentionally attempted to pass a typed-provider factory to
the production adapter and failed because that boundary did not yet exist:

```text
logs/TDD_RED_S5_GRAPHITI_PRODUCTION_ADAPTER_INTEGRATION_20260815.xml
```

The minimum implementation added an explicit
`controlled_provider_factory`. The factory converts the public FX0 provider
projection into one typed `ControlledGraphitiProviders` object; the same
object is used by case reset, source decoding, prepare, and bind. The legacy
identity conversion remains only for pre-existing offline adapter tests, while
the production artifact validator requires an explicit factory. The real
integration GREEN test is:

```text
logs/TDD_GREEN_S5_GRAPHITI_PRODUCTION_ADAPTER_INTEGRATION_20260815.xml
tests/test_s5_graphiti_production_adapter_integration.py
```

It executes the installed pinned Graphiti functions through the adapter and
observes the Native call order, one transaction attempt, typed provider scope
exit, and durable snapshot evidence. A factory returning no provider is also
tested as fail-closed. This is still bounded offline execution; it does not
prove the complete FX0 transition inventory or production retry idempotence.

The follow-on integration uses two real sources and observes prepare overlap,
source-ordered publication, and two transaction commits. A separate retry case
uses the fixture's complete durable-row projection as a witness for a real
Graphiti transaction callback replay. The adapter now reports both
`transaction_attempt_count` and the aggregate attempt count; a retry witness
without at least two actual attempts remains fail-closed.

Latest retry-witness evidence:

```text
logs/TDD_RED_S5_GRAPHITI_TRANSACTION_RETRY_WITNESS_20260815.xml
logs/TDD_GREEN_S5_GRAPHITI_TRANSACTION_RETRY_WITNESS_20260815.xml
logs/TDD_FOCUSED_GREEN_S5_GRAPHITI_RETRY_WITNESS_FINAL_20260815.xml
logs/TDD_FULL_OFFLINE_GREEN_S5_GRAPHITI_RETRY_WITNESS_20260815.xml
```

The full offline regression at this checkpoint is `1108 passed`, with zero
failures, errors, and skips and one upstream Pydantic deprecation warning.
This is production-path retry evidence for the bounded controlled fixture; it
does not by itself seal the all-transition FX0 artifact.

The final execution-shape extension in this checkpoint changes the controlled
latest-state provider after real prepare and verifies that bind observes the
new valid `EpisodicNode`. The focused execution-shape suite remains green, and
the latest full offline regression is now `1110 passed`, with zero failures,
errors, and skips and one upstream warning:

```text
logs/TDD_GREEN_S5_GRAPHITI_PREPARE_BIND_STATE_CHANGE_20260815.xml
logs/TDD_FOCUSED_GREEN_S5_GRAPHITI_EXECUTION_SHAPES_20260815.xml
logs/TDD_FULL_OFFLINE_GREEN_S5_GRAPHITI_EXECUTION_SHAPES_20260815.xml
```

The observed state-change, compatible duplicate coalescing, source-order, and
transaction-retry cases are still bounded transition evidence. No production
FX0 artifact or live authority follows from them.

## Independent publication-fault detector checkpoint

The adapter now accepts an explicit publication-fault detector that sees only
the observed durable snapshot/history and source count. It does not receive
fixture expected state/status/history and it rejects non-registered detector
results. Controlled history sinks exercise all three required modes through
the real pinned Graphiti path:

```text
LOST_PUBLICATION       one source's publication silently absent
DUPLICATE_PUBLICATION  one source appears twice in durable history
PARTIAL_PUBLICATION    one of two source publications is absent
```

Each mode produces `FAIL_CLOSED` with the registered error code before the
adapter returns a mergeable result. Evidence is in
`logs/TDD_RED_S5_GRAPHITI_PUBLICATION_FAULT_DETECTOR_20260815.xml`,
`logs/TDD_GREEN_S5_GRAPHITI_PUBLICATION_FAULT_DETECTOR_20260815.xml`, and
`logs/TDD_GREEN_S5_GRAPHITI_PUBLICATION_FAULT_MODES_20260815.xml`.

The latest complete offline regression is `1114 passed`, with zero failures,
errors, and skips and one upstream Pydantic deprecation warning:
`logs/TDD_FULL_OFFLINE_GREEN_S5_GRAPHITI_PUBLICATION_FAULTS_FINAL_20260815.xml`.
This remains controlled fault-injection evidence; no production FX0 artifact
or live authority is generated.

`compileall` and `git diff --check` passed. No model, embedding, network, or
Neo4j calls were made.

## What The Real Fixture Executes

The fixture invokes the actual pinned Graphiti functions, not paper-eval
callback doubles, for:

```text
extract_nodes
resolve_extracted_nodes
extract_edges
resolve_edge_pointers
resolve_extracted_edges
extract_attributes_from_nodes
Graphiti._process_episode_data
```

The observed call order now matches Graphiti 0.29.3 `add_episode()`:

```text
extract_nodes
  -> resolve_extracted_nodes
  -> extract_edges
  -> resolve_edge_pointers
  -> resolve_extracted_edges
  -> extract_attributes_from_nodes
  -> _process_episode_data
```

The following bounded cases are green:

- Native default edge-type map when `edge_type_map` is absent.
- Existing canonical node resolution through the real candidate-search and
  similarity path.
- Real edge extraction and pointer/resolution path followed by bulk commit.
- Real temporal invalidation, with the old edge updated before the commit
  bulk is observed.
- Explicit `group_id` database clone routing.
- Commit publication only after the real `_process_episode_data` transaction
  callback returns successfully.
- Malformed native commit return shape is rejected fail-closed.
- A transaction callback replay is detected; the fixture refuses to claim
  retry idempotence and emits no publication.
- The provider ledger observes only the declared provider boundaries, and an
  explicit case reset restores mutable candidate/edge/event state.
- Two independent real Graphiti sources commit and publish in source order.
- A controlled durable upsert witness allows retry idempotence only when the
  complete row projection is equal across attempts; a same-UUID payload change
  is rejected.

## Important Boundary

The default retry test is intentionally a negative qualification.
Graphiti/Neo4j `execute_write` may replay a transaction callback. The
controlled fixture can produce a bounded upsert witness, but this is not yet
the production FX0 retry evidence because the complete transition inventory
and M* scheduler execution shape remain pending. Any changed durable row
projection still fails closed. This is a blocking evidence gap, not a failed
full regression.

The fixture also does not yet provide the complete FX0 transition inventory,
the final production FX0 artifact. The multi-source fixture is a bounded
commit-order witness, not yet the full M* scheduler execution-shape proof.
The separate production artifact builder remains fail-closed and
the legacy FX0 self-test schema is unchanged.

## Authority And Current Stage

`runtime/CURRENT_STAGE_STATUS.json` remains unchanged:

```text
current_stage = S3_CONFIGURATION_FROZEN
model_call_authorized = false
neo4j_read_authorized = false
neo4j_mutation_authorized = false
s5_live_execution_authorized = false
current_stage_update_authorized = false
```

The next permitted action is further offline TDD for the complete FX0
transition inventory and production-bound retry/idempotence evidence. No live
action follows from this checkpoint.
