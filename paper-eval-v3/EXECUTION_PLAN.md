# Paper Evaluation v3 Execution Plan

Protocol source: `../（主实验）MemBind_PAPER_EVALUATION_WORKPLAN_v3_SMALL_FIRST_FINAL.md`

Protocol SHA256: `4b81c89b33d407f04fc20862a81eab6badba16d0d61d98331cbe188d1bb4f41e`

## Scope

This is a new, isolated lane. Existing C0-C5 contracts and artifacts remain historical evidence and are not rewritten. v3 forbids C6, so this lane never schedules C6.

## S2 retrieval-contract amendment

The completed `s2-live-20260814-001` run exposed an interface mismatch: it
ranked Graphiti EntityEdges but labeled the result as LongMemEval
`flat-session` retrieval. The historical run and artifacts remain immutable.
Its numeric retrieval field is now interpreted only as Edge@10-attributed
source-session coverage; official LongMemEval session Recall@10 was not
computed.

Future code uses an explicit `graphiti_basic_edge` contract, adapter identity
v2, edge-unit metric names, and hashes of the underlying Graphiti search
implementation and recipes. S3 remains unauthorized. The analysis and bounded
conditional `S2-R0` decision procedure are frozen in
`S2_RETRIEVAL_SURFACE_ANALYSIS_20260814.md`; no `S2-R0` live call is authorized
by this amendment itself.

### v3.1 interpretation overlay

`../MemBind_PAPER_EVALUATION_PROTOCOL_AMENDMENT_v3.1.md` is the controlling
overlay for retrieval units and S2 recovery. It does not mutate the parent
protocol (SHA256
`4b81c89b33d407f04fc20862a81eab6badba16d0d61d98331cbe188d1bb4f41e`)
or any completed artifact.

The only candidate next live action is S2-R0: a read-only, episode-only
Graphiti 0.29.3 full-text/RRF probe over the immutable S1 namespace. It may be
authorized only after corpus ID/hash/mapping completeness, exact diagnosis
scope, fresh-config, zero-model-call, and result-sealing tests all pass. S2-R0
uses LongMemEval session-metric semantics but is not the official LongMemEval
retriever implementation. S3 remains unauthorized until the probe is sealed
and the evaluation policy is frozen offline.

## Ordered stages

1. **S0**: read-only current-state and reuse audit; produce three finalized JSON artifacts.
2. **S1**: one fixed calibration history (`07741c45`), pinned upstream U0, serial episode smoke with durable event/checkpoint recovery.
3. **S2**: dataset/evaluator alignment, C2 reuse decision, and U0 sanity.
4. **S3**: freeze U0.
5. **S4-S6**: D0 qualification, method smoke, and development-only concurrency sweep.
6. **S7-S8**: bounded pilot and outcome-independent precision planning.
7. **S9-S10**: freeze and run formal paper evaluation, then write the headline table and stop.

## Detailed execution gates

| Stage | Offline gate | Authorized live work | Durable output | STOP condition |
|---|---|---|---|---|
| S0 | artifact envelope, role-disjointness, secret-scan tests | none | three finalized S0 JSON files | missing or contradictory provenance |
| S1 | dataset binding, namespace rebinding/probe, source-order, failure/resume tests | one `07741c45` U0 construction and one retrieval | per-episode JSONL, atomic checkpoint, final summary | loss, duplicate, order drift, retrieval failure, service disconnect, or namespace mismatch |
| S2 | dataset/evaluator parity and C2 equivalence decision tests | only missing U0/reference sanity allowed by the decision tree | alignment report and `C2_U0_REUSE_DECISION.json` | parity failure or unexplained near-zero sanity result |
| S3 | freeze schema/hash tests | none | `NATIVE_BASELINE_FREEZE.json` | incomplete U0 identity |
| S4 | capture/replay coverage and parity tests | one history, then at most four exposed histories | D0 qualification/freeze | oracle miss, fallback, coverage failure, or unexplained semantic drift |
| S5 | A0/P/M scheduler and invariant-fixture tests | one exposed history per method, sequential stage order | method smoke artifacts | any method-specific hard gate failure; M failure blocks sweep |
| S6 | selection-rule tests | fixed four exposed histories, `C={1,2,4,8}` | `METHOD_SELECTION_FREEZE.json` | M has no qualified concurrency |
| S7 | deterministic role/selection tests | frozen eight-history pilot plus preregistered repeats | pilot manifest/results | correctness or systems signal fails the frozen continuation rule |
| S8 | estimator/bootstrap and outcome-independence tests | none | sample-size plan | available held-out pool cannot meet precision plan |
| S9 | complete freeze validation | none | final manifest/config/statistics freeze | any mutable or overlapping final identity |
| S10 | resume/aggregation/statistics tests | frozen formal blocks only | per-block checkpoints, results, headline table | frozen STOP/failure rule; never add samples based on significance |

No stage may create the next stage's live namespace before its offline gate is green.
S0-S1 use a new `pev3-*` namespace and never modify old C0-C5 namespaces or
`membind-validation/CURRENT_STATE.json`.

## TDD gate for every stage

Before any model or database call:

1. Add/execute a failing offline contract test (RED).
2. Implement the smallest behavior that satisfies it.
3. Run focused GREEN tests.
4. Run the complete offline regression for this lane.
5. Only then perform the stage's authorized live action.

## Durability rules

Events are JSONL records written with flush + `fsync`; checkpoints are written to a temporary file, flushed + `fsync`ed, then atomically replaced. A resume may process only the first not-yet-published source sequence. A non-empty namespace without a matching durable checkpoint fails closed.

## Current execution log

The stage ledger is maintained in `runtime/STAGE_STATUS.json` and all stage artifacts live below `artifacts/paper_eval/`.

Current status is `S2 STOP`: S0 and S1 passed, the historical edge-surface S2
chain is terminal/non-mergeable, the retrieval-contract review is complete,
and S3 has no authorization.

## S1 tmux operations

Start or resume the one authorized S1 run with:

```bash
./scripts/run_s1_tmux.sh <run-id> <fresh-namespace>
```

The script refuses a duplicate session. Re-running it after an SSH disconnect with the same IDs resumes the durable prefix; it never starts a second controller or cleans an old namespace. Inspect with `tmux attach -t membind-pev3-s1-<run-id>` or `tmux capture-pane -pt membind-pev3-s1-<run-id>`, and inspect the checkpoint under `artifacts/paper_eval/native/runs/<run-id>/`.

Long live commands run in a detached `tmux` session named for the stage and run
ID. Console output is line-buffered into `logs/`; scientific state is taken only
from the durable JSONL/checkpoint artifacts, not from terminal output.
