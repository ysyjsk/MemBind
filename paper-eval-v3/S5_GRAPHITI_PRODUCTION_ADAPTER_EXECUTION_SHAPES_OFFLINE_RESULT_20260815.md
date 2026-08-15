# S5 Graphiti Production-Adapter Execution Shapes Offline Result

Date: 2026-08-15

## Scope

This checkpoint is limited to offline TDD in the isolated `paper-eval-v3`
lane. It does not start vLLM, an embedding service, Neo4j, a namespace, or a
live S5 runner. The pinned local Graphiti dependency is 0.29.3 at commit
`021d3a57d511f21b10adaf7fa923bd5c1fce5e9d`.

## Implemented Boundaries

- `controlled_provider_factory` converts the public FX0 provider projection to
  one typed `ControlledGraphitiProviders` object shared by reset, source decode,
  prepare, and bind.
- The adapter invokes the actual pinned Graphiti sequence:

  ```text
  extract_nodes
  -> resolve_extracted_nodes
  -> extract_edges
  -> resolve_edge_pointers
  -> resolve_extracted_edges
  -> extract_attributes_from_nodes
  -> _process_episode_data
  ```

- Two real sources demonstrate prepare overlap, source-ordered bind/publication,
  and two transaction commits.
- A same-UUID/same-projection duplicate produced through real node resolution
  is deterministically coalesced; the existing semantic contract still rejects
  same-UUID/different-projection input before attributes or commit.
- A valid latest-state object injected after prepare is observed by bind.
- A controlled `execute_write` replay with equal complete durable-row
  projections reports `transaction_attempt_count=2` and one logical
  publication.
- An independent publication-history detector catches:

  ```text
  LOST_PUBLICATION
  DUPLICATE_PUBLICATION
  PARTIAL_PUBLICATION
  ```

  Unknown or non-string detector results fail closed. Expected fixture state,
  status, history, and error code never cross the adapter callback boundary.

## TDD Evidence

```text
RED   logs/TDD_RED_S5_GRAPHITI_PRODUCTION_ADAPTER_INTEGRATION_20260815.xml
GREEN logs/TDD_GREEN_S5_GRAPHITI_PRODUCTION_ADAPTER_INTEGRATION_20260815.xml
RED   logs/TDD_RED_S5_GRAPHITI_TRANSACTION_RETRY_WITNESS_20260815.xml
GREEN logs/TDD_GREEN_S5_GRAPHITI_TRANSACTION_RETRY_WITNESS_20260815.xml
RED   logs/TDD_RED_S5_GRAPHITI_PUBLICATION_FAULT_DETECTOR_20260815.xml
GREEN logs/TDD_GREEN_S5_GRAPHITI_PUBLICATION_FAULT_DETECTOR_20260815.xml
GREEN logs/TDD_GREEN_S5_GRAPHITI_PUBLICATION_FAULT_MODES_20260815.xml
GREEN logs/TDD_GREEN_S5_GRAPHITI_PREPARE_BIND_STATE_CHANGE_20260815.xml
FOCUSED logs/TDD_FOCUSED_GREEN_S5_GRAPHITI_PUBLICATION_FAULTS_FINAL_20260815.xml
FULL logs/TDD_FULL_OFFLINE_GREEN_S5_GRAPHITI_PUBLICATION_FAULTS_FINAL_20260815.xml
```

The final full offline regression is:

```text
1114 passed
0 failed
0 errors
0 skipped
1 upstream Pydantic deprecation warning
```

`compileall` and `git diff --check` also pass.

## Qualification Boundary

This is bounded production-path adapter evidence, not a complete FX0 exact
parity artifact. The all-transition fixture rows have not been assembled and
verified together, and no production FX0 artifact was generated. The legacy
FX0 self-test artifact and the historical
`S5_MSTAR_FX0_PRODUCTION_PARITY_STATUS_20260815.json` remain unchanged.

The current pointer and authority remain:

```text
current_stage = S3_CONFIGURATION_FROZEN
model_call_authorized = false
neo4j_read_authorized = false
neo4j_mutation_authorized = false
s5_live_execution_authorized = false
current_stage_pointer_update_authorized = false
```
