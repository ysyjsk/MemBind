# S5 Graphiti FX0 Production Qualification Result

Date: 2026-08-16

## Verdict

```text
PRODUCTION_PATH_EXACT_PARITY_PASS
fixture_count = 11
Graphiti = 0.29.3 @ 021d3a57d511f21b10adaf7fa923bd5c1fce5e9d
external model calls = 0
embedding service calls = 0
Neo4j reads/writes = 0 / 0
current-stage pointer updates = 0
```

This is a controlled offline qualification of the pinned production semantic
path. It is not a live M* smoke, quality estimate, performance result, PILOT,
or formal run.

## Exact Transition Results

| Transition | Rows | Observed result |
| --- | ---: | --- |
| Entity alias canonical merge | 1 | PASS; canonical existing node persisted |
| Compatible duplicate UUID coalescing | 1 | PASS; real resolver `pre=2`, coalescer `post=1` |
| Conflicting duplicate UUID | 1 | FAIL_CLOSED; `CONFLICTING_DUPLICATE_UUID`; no commit/publication |
| Relation resolution | 1 | PASS; real `WorksAt` edge and logical endpoints persisted |
| Temporal invalidation | 1 | PASS; old edge persisted with exact `invalid_at` update |
| Prepare-to-bind state change | 1 | PASS; two-source overlap and latest-state advance observed |
| Source-ordered publication | 1 | PASS; publication order exactly `[0, 1]` |
| Retry idempotence | 1 | PASS; two transaction callbacks, equal durable projections, one publication |
| Lost publication detection | 1 | FAIL_CLOSED; `LOST_PUBLICATION` derived from observed history |
| Duplicate publication detection | 1 | FAIL_CLOSED; `DUPLICATE_PUBLICATION` derived from observed history |
| Partial publication detection | 1 | FAIL_CLOSED; `PARTIAL_PUBLICATION` derived from observed history |

Seven rows have the registered `PASS` outcome class. Four rows have the exact
registered fail-closed outcome classes: one conflicting duplicate plus the
three publication fault modes. Failure directives and expected errors are not
present in source data or provider schedules.

## Production Boundary

The final adapter boundary enforces all of the following:

- `controlled_provider_factory` receives only the hash-bound provider plan,
  never `case`, `case_id`, source text, transition, or oracle fields;
- source decoding accepts only a strict Graphiti episode-batch schema and
  recursively rejects transition/error/expected/fault/verdict directives;
- LLM, embedding, logical time, initial state, candidate sets, transaction I/O
  schedule, and publication sink schedule are all included in the production
  provider hash;
- the legacy five-provider self-test projection remains unchanged, so the old
  self-test artifact is not retroactively rewritten;
- semantic `prepare` and `bind` are the same bound
  `S5GraphitiMStarSemanticRuntime` methods;
- snapshot, event sink, source decoder, reset, witness, provider factory,
  fault detector, clock, provider scope, and latest-state retriever belong to
  one generic controlled environment owner;
- fixture input/provider/oracle/manifest hashes are independently recomputed
  from the actual 11-row spec before any adapter execution and again from the
  sealed case evidence;
- changing a retry run ID does not change the frozen fixture manifest identity.

## TDD Evidence

Representative RED evidence:

```text
logs/TDD_RED_S5_GRAPHITI_CONFLICT_FAILURE_MAPPING_20260815.xml
logs/TDD_RED_S5_FX0_PROVIDER_FACTORY_ORACLE_ISOLATION_20260815.xml
logs/TDD_RED_S5_FX0_PROVIDER_SCHEDULE_HASH_20260815.xml
logs/TDD_RED_S5_GRAPHITI_FX0_ENVIRONMENT_SOURCE_ISOLATION_20260815.xml
logs/TDD_RED_S5_FX0_CONTROLLED_ENVIRONMENT_OWNER_BINDING_20260815.xml
logs/TDD_RED_S5_FX0_FIXTURE_BINDING_DERIVATION_20260815.xml
logs/TDD_RED_S5_GRAPHITI_FX0_TWO_SOURCE_WITNESS_20260815.xml
logs/TDD_RED_S5_GRAPHITI_FX0_COMPLETE_INVENTORY_20260815.xml
logs/TDD_RED_S5_GRAPHITI_FX0_COALESCING_SHAPE_20260815.xml
logs/TDD_RED_S5_GRAPHITI_FX0_FINALIZER_20260815.xml
```

Final GREEN evidence:

```text
inventory exact parity       passed
production artifact E2E      passed
exclusive finalizer          passed
full offline regression      1151 passed
failures/errors/skips        0 / 0 / 0
compileall                   passed
git diff --check             passed
upstream warnings            1 Graphiti/Pydantic deprecation warning
```

Primary final logs:

```text
logs/TDD_GREEN_S5_GRAPHITI_FX0_INVENTORY_EXACT_PARITY_20260815.xml
logs/TDD_GREEN_S5_GRAPHITI_FX0_COALESCING_SHAPE_20260815.xml
logs/TDD_GREEN_S5_GRAPHITI_FX0_PRODUCTION_ARTIFACT_E2E_20260815.xml
logs/TDD_GREEN_S5_GRAPHITI_FX0_FINALIZER_20260816.xml
logs/TDD_FULL_OFFLINE_GREEN_S5_GRAPHITI_FX0_FINAL_20260816.xml
```

## Sealed Artifacts

```text
artifacts/paper_eval/native/S5_MSTAR_FX0_RUNTIME_CONFIG_20260816.json
  file SHA256 = d767b51048e4d168b3e1d3f10234d91cf7ae1bd45976946cb3bda9bee902af7f
  payload SHA256 = 41fcfd6d15b0c6eeb7a0ef5aec6cf9260454d3f91287a26bce27804cbe009d2c

artifacts/paper_eval/native/S5_MSTAR_PRODUCTION_CORE_IDENTITY_20260816.json
  file SHA256 = 6d1186a7295a03084bc18eb2ba20f40fe8d206104d4b76e9a9ccbfc51db3a7fb
  identity SHA256 = a2c3b71154de884771363317241af438fdf6ea3ddaf1202e1b032016a37b02c9

artifacts/paper_eval/native/S5_MSTAR_FX0_PRODUCTION_PARITY_20260816.json
  file SHA256 = f3f9c5061aaca00900714d7c8fb2054d13a442395d8cfa5ce68953e1e8b3c493
  payload SHA256 = 196ac96bcec7e97fe4ba29bc7ce600fc169bad7f4b825ef7791f12dc1e622722
  fixture manifest SHA256 = f40981830d02db7c13adf17064ce24ee47e1b2349c4674618cb5c1ff6d4b8a9d

artifacts/paper_eval/native/S5_MSTAR_FX0_QUALIFICATION_20260816.json
  file SHA256 = 0b3431012bb7e2224b6d007bf2e14a886fcbe1dbf1f2f6be004cbdda8523ad43
```

The full regression JUnit hash is
`d66981aee7ad24a8c1b20f6e90c1371a9a416700446d22b101928e3a5ce48468`.

## Preserved State And Next Gate

`runtime/CURRENT_STAGE_STATUS.json` remains byte-identical at SHA256
`3cb7edad4bab3ac6fe961a3d9e8768cbb962cf61cf946cb7e0015d74c0edc26d`.
The historical non-executed status artifact remains byte-identical at SHA256
`15b2d41f2f1ee6416d6d1a58cfaf5f1f9143eddcc62b5b05ee98b9059d45db56`.

All authority remains false:

```text
model_call_authorized = false
neo4j_read_authorized = false
neo4j_mutation_authorized = false
s5_live_execution_authorized = false
current_stage_pointer_update_authorized = false
```

This result clears the offline production FX0 parity gate only. It does not
bypass the frozen A0/P* smoke prerequisites and does not issue the separate,
single-use authority required for an M* live smoke.
