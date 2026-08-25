# MemBind v1.3 Execution Status

## Frozen and Verified

- Dataset: MemoryAgentBench Accurate Retrieval, `longmemeval_s*`.
- Authority: full 5-context component; session counts `111/107/116/111/110`; 555 sessions; 300 QA.
- Local dataset SHA-256: `97fd80207f3419fc57c3684db824334224546d6bdd62c17ef52cd116eec9ffc8`.
- Known provenance issue: `0ddfec37_abs` retained as `PARTIAL_GOLD_MAPPING`; evidence metrics are null.
- Renderer hash is recorded in `../mab-v1-3-authority-20260824-002/frozen_config.json`.
- Formal plan: 45 fresh blocks and 2700 planned QA rows, with balanced arm order.

## TDD Evidence

- MAB package regression: 38 passed, 2 skipped.
- v1.3 package regression: 243 passed.
- New provider-free contract tests: 22 passed.
- `compileall` and `git diff --check`: passed.
- RED/GREEN evidence: `../mab-v1-3-authority-20260824-002/tdd_evidence.json`.

## Diagnostic Evidence

The context-0 prefix8 provider-free triad completed for B0/B1/V6. All three
arms shared one workload hash and durable makespan boundary. B0 and V6 passed
the ordered predicate; B1 was correctly marked `NOT_REQUIRED` and recorded 7
observed inversions; V6 exact binding passed. These artifacts are diagnostic
only and cannot enter the formal reducer.

## External Blocker

The read-only live preflight is `BLOCKED_EXTERNAL_PROVIDER`: both frozen model
endpoints (`10.87.5.247:8000` and `:8001`) are unreachable by direct TCP probe.
Neo4j TCP is reachable. No formal construction or QA result is claimed, and no
dataset, method, arrival policy, metric, or namespace was changed to work around
the blocker. The preserved preflight evidence is
`../mab-v1-3-live-preflight-20260824-003.json`.
