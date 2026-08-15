# Paper Evaluation v3 Execution Plan

Protocol source: `../（主实验）MemBind_PAPER_EVALUATION_WORKPLAN_v3_SMALL_FIRST_FINAL.md`

Protocol SHA256: `4b81c89b33d407f04fc20862a81eab6badba16d0d61d98331cbe188d1bb4f41e`

## Scope

This is a new, isolated lane. Existing C0-C5 contracts and artifacts remain historical evidence and are not rewritten. v3 forbids C6, so this lane never schedules C6.

## Current Reader-v2 overlay (2026-08-14)

`NATIVE_READER_V2_QUALIFICATION_WORKPLAN_v1.0.md` is the controlling overlay
for the common Reader after the historical direct-Reader completion returned
`Recall_all@10=1.0` and `QA=0.0` on exposed item `07741c45`. It preserves that
result and every earlier S2 artifact.

The overlay keeps Graphiti construction, Episode BM25/RRF retrieval, K=10,
session materialization, chronological presentation, JSON, both conversation
sides, and the Judge fixed. It versions only the answer Reader to the pinned
LongMemEval repository's recommended single-call recipe:

```text
READING_METHOD=con -> cot=true, con=false
max_tokens=800
one Reader request
```

The selection is explicitly non-blind and occurred after the direct failure.
The disjoint DEVELOPMENT_EXPOSED canary `b6019101` passed exact adapter
compatibility with one search, one Reader, one Judge, zero truncation, and zero
retry. Its QA value is diagnostic and was not a freeze gate. The detailed
result and caveats are in
`NATIVE_READER_V2_QUALIFICATION_RESULT_REPORT_20260814.md`.

`artifacts/paper_eval/native/NATIVE_READER_V2_FREEZE.json` now binds the same
Reader and Judge identities for U0/A0/P*/M*. It authorizes only an S3
configuration update. It does not authorize PILOT, S4, a retrieval/top-k
sweep, graph reconstruction, or any quality claim.

## S3 Native-v2 configuration freeze (2026-08-14)

The Reader-v2 configuration update is complete. The additive artifact
`artifacts/paper_eval/native/NATIVE_BASELINE_V2_FREEZE.json` binds the existing
Native Graphiti construction, Episode BM25/RRF retrieval at K=10, dataset,
role snapshot, Reader-v2, and Judge identities for U0/A0/P*/M*. The historical
`s3_freeze.py` Gate-C implementation and all earlier failed/diagnostic S2
artifacts remain untouched.

This is deliberately a configuration freeze, not a claim that historical S2
quality passed:

```text
configuration_freeze_only       true
s2_quality_pass_claimed          false
quality_estimate_status          NOT_ESTIMATED
s4_live_execution_authorized     false
pilot_execution_authorized       false
```

The proposal to stop expanding the baseline is now frozen as an execution
constraint: do not add an 8-16 item qualification wave, a K sweep, another
benchmark, another baseline, a retrieval redesign, or a model change. Aggregate
Native/MemBind quality belongs in the later disjoint PILOT. The Reader-v2
adapter reuses pinned LongMemEval prompt/materialization semantics; it is not
described as an unmodified upstream Qwen3 runner.

### Baseline-closure interpretation

The Native-v2 baseline lane is closed after the S3 configuration freeze. The
historical direct Reader was a supported, hash-bound LongMemEval path rather
than an invalid ad-hoc reader, and the Reader-v2 canary establishes adapter
compatibility only. It neither estimates aggregate Native quality nor proves
that CoN repairs the historical failed item.

The remaining S4-S6 stages must not be interpreted as further baseline tuning:

```text
S4  deterministic D0 correctness control
S5  one-history A0/P*/M* method smoke
S6  fixed development-only concurrency selection
```

They remain necessary before the disjoint S7 PILOT because they establish the
oracle, semantic-parity, and qualified-method identities needed to interpret a
Native-versus-MemBind result. They may not change Reader, Judge, retrieval,
K=10, dataset, construction model, or the frozen workload. The S4 `1 + fixed
four DEVELOPMENT_EXPOSED histories` scope is a D0 correctness qualification,
not the rejected 8-16-item Native/Reader quality qualification.

S4 capture and replay use different Neo4j isolation namespaces. The pinned
Graphiti language-instruction function is group-ID invariant, but the legacy
prompt oracle includes `group_id` as non-semantic cache metadata. The isolated
S4 adapter therefore projects only that cache-key field to
`__S4_ISOLATED_NAMESPACE__`; the real Graphiti call, messages, system content,
user content, and Neo4j group IDs remain unchanged. This projection is bound by
the S4 production-adapter source and tests and is not available to later
performance treatments.

S0's declared construction repository revision and the revision constant in
the S1-bound runtime source differ. The constant did not enter the model
request and cache was disabled, so prior S1 evidence remains usable, but the
freeze records this as `CONFLICT_DISCLOSED` rather than claiming a current live
attestation. A lightweight service-identity preflight is required before any
S4 live action.

## S2 retrieval-contract amendment

The completed `s2-live-20260814-001` run exposed an interface mismatch: it
ranked Graphiti EntityEdges but labeled the result as LongMemEval
`flat-session` retrieval. The historical run and artifacts remain immutable.
Its numeric retrieval field is now interpreted only as Edge@10-attributed
source-session coverage; official LongMemEval session Recall@10 was not
computed.

Historical recovery code uses an explicit `graphiti_basic_edge` contract,
adapter identity v2, edge-unit metric names, and hashes of the underlying
Graphiti search implementation and recipes. The analysis and bounded
conditional `S2-R0` decision procedure are frozen in
`S2_RETRIEVAL_SURFACE_ANALYSIS_20260814.md`; no `S2-R0` live call is authorized
by this amendment itself.

### v3.1 interpretation overlay

`../MemBind_PAPER_EVALUATION_PROTOCOL_AMENDMENT_v3.1.md` is the controlling
overlay for retrieval units and S2 recovery. It does not mutate the parent
protocol (SHA256
`4b81c89b33d407f04fc20862a81eab6badba16d0d61d98331cbe188d1bb4f41e`)
or any completed artifact.

The historical candidate action was S2-R0: a read-only, episode-only
Graphiti 0.29.3 full-text/RRF probe over the immutable S1 namespace. It may be
authorized only after corpus ID/hash/mapping completeness, exact diagnosis
scope, fresh-config, zero-model-call, and result-sealing tests all pass. S2-R0
uses LongMemEval session-metric semantics but is not the official LongMemEval
retriever implementation. That recovery chain is now sealed historical
evidence; the selected Episode retrieval policy is bound by the S3-v2 freeze.

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
| S3 | freeze schema/hash tests | none | `NATIVE_BASELINE_V2_FREEZE.json` | incomplete U0 identity or hidden quality claim |
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

The immutable historical stop ledger remains in `runtime/STAGE_STATUS.json`.
The additive current pointer is `runtime/CURRENT_STAGE_STATUS.json`; all stage
artifacts live below `artifacts/paper_eval/`.

The durable current pointer remains `S3_CONFIGURATION_FROZEN`; it is not
advanced by a failed qualification. S0 and S1 execution evidence passed, while
historical edge/direct-Reader attempts remain immutable. S3-v2 freezes only
the Native/common evaluation configuration and does not estimate quality.

The first valid S4 smoke capture (`retry-004`) completed 49/49 episodes, but
its D0 replay stopped after 2/49 episodes on one `UnexpectedPromptError`.
Diagnosis proved that D0 reordered an unchanged two-node candidate set and
therefore reassigned the position-indexed `candidate_id` values. The failed
replay is `INCOMPLETE_DIAGNOSED_NON_MERGEABLE`; its exact namespace was cleaned
to zero nodes and relationships. The sealed result is documented in
`S4_D0_SMOKE_RESULT_REPORT_20260815.md`.

`S4_CANDIDATE_INDEX_REMAP_AMENDMENT_v1.0.md` records the retry-005 repair. Its
U0 capture passed 49/49 episodes. D0 replay then completed source sequences
`0..6` and failed closed at source sequence 7 with:

```text
CandidateRemapError
AMBIGUOUS_CANDIDATE_IDENTITY
edge invalidation candidate partition
```

The replay used zero live LLM, embedding, fallback, unexpected-oracle, and
cross-encoder calls. Prompt and embedding cache hashes remained identical to
capture. The current prompt-visible edge identity could not establish a unique
capture-to-replay candidate bijection, so positional response translation
correctly refused to guess. Retry-005 is incomplete and non-mergeable. Its
replay namespace is intentionally preserved at 32 nodes and 48 relationships;
no cleanup or new attempt is authorized.

The detailed evidence is in
`S4_D0_REMAP_RETRY_005_FAILURE_REPORT_20260815.md`. The four-history S4
qualification, S5, and PILOT remain unauthorized. The sealed original
qualification plan remains byte-identical and non-authorizing.

An additive qualification-activation module was prepared while capture was
stable. It re-runs the strict retry-005 result verifier and can activate only
the exact sealed fixed-four plan; it continues to deny S5 and PILOT. The full
offline gate is now 632 tests passing. Because retry-005 did not produce
`S4_D0_REMAP_SMOKE_RESULT.json`, no activation artifact was generated.

The next action is an explicit offline design decision about ambiguous edge
candidate identity. Do not clean the failed namespace, allocate retry-006,
relax the fail-closed oracle, or start qualification automatically.

## S1 tmux operations

Start or resume the one authorized S1 run with:

```bash
./scripts/run_s1_tmux.sh <run-id> <fresh-namespace>
```

The script refuses a duplicate session. Re-running it after an SSH disconnect with the same IDs resumes the durable prefix; it never starts a second controller or cleans an old namespace. Inspect with `tmux attach -t membind-pev3-s1-<run-id>` or `tmux capture-pane -pt membind-pev3-s1-<run-id>`, and inspect the checkpoint under `artifacts/paper_eval/native/runs/<run-id>/`.

Long live commands run in a detached `tmux` session named for the stage and run
ID. Console output is line-buffered into `logs/`; scientific state is taken only
from the durable JSONL/checkpoint artifacts, not from terminal output.
