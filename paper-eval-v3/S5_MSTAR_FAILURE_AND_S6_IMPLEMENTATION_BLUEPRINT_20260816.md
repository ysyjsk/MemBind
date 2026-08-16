# S5 M* Failure Analysis and S6 Implementation Blueprint

Date: 2026-08-16

Status: `ANALYSIS_ONLY_NON_AUTHORIZING`

This document is an additive engineering note. It does not amend the frozen
paper-evaluation protocol, authorize a model call or Neo4j operation, alter a
current-stage pointer, permit cleanup/resume of a failed attempt, or authorize
S6. The controlling protocol remains:

```text
../（主实验）MemBind_PAPER_EVALUATION_WORKPLAN_v3_SMALL_FIRST_FINAL.md
```

## 1. Current Gate

The frozen execution order remains:

```text
S5 A0 smoke
  -> S5 P*(C=2) smoke
  -> current-source M* FX0 qualification
  -> S5 M*(C=2) live smoke
  -> S6 development-only concurrency calibration
```

The first three entries completed. The M* live smoke reached a terminal,
fail-closed state, so S6 is currently blocked.

Canonical predecessor evidence:

```text
A0:
  artifacts/paper_eval/native/runs/
  s5-a0-20260816-004/S5_A0_RESULT.json

P*(C=2):
  artifacts/paper_eval/native/runs/
  s5-p-star-20260816-001/S5_PSTAR_RESULT.json

M* FX0 current-source qualification:
  artifacts/paper_eval/native/
  S5_MSTAR_FX0_QUALIFICATION_CURRENT_HEAD_20260816.json
```

Failed M* live evidence:

```text
artifacts/paper_eval/native/runs/s5-mstar-20260816-001/
  authority_consumption.json
  controller/events.jsonl
  controller/checkpoint.json
  attempt/manifest.json
  attempt/events.jsonl
  attempt/publication_journal.jsonl
  attempt/checkpoint.json
  attempt/result.json

logs/s5-mstar-20260816-001.log
```

## 2. Evidence Maturity

The word "accuracy" must not combine implementation tests with downstream QA
quality. The current evidence has separate scopes:

| Surface | Current evidence | Strongest allowed interpretation |
|---|---|---|
| Offline implementation correctness | 1,420/1,420 tests passed; zero failures, errors, or skips | Strong regression and fail-closed contract evidence for the tested cases |
| M* deterministic mechanism correctness | 11 controlled FX0 transitions have exact production-path parity | Exact parity on the frozen transition inventory only |
| A0 live construction correctness | One development-exposed history, 49/49 episodes, no loss/duplicate/fallback/direct violation | Bounded one-history production smoke PASS |
| P*(C=2) live construction behavior | One development-exposed history, 49/49 episodes, real overlap, zero observed direct violation | No naive-parallel insufficiency observed in this one screening run |
| M* live construction correctness | 49/49 prepared, 8/49 committed and published in source order, then fail-closed | Prepare overlap and ordered-prefix behavior observed; complete M* smoke not qualified |
| Reader/Judge adapter compatibility | One non-mergeable canary with diagnostic QA=1 and Recall@10=1 | Adapter compatibility only, not a quality estimate |
| Historical U0 reference sanity | One exposed item had QA=0 and Recall@10=0 before the Reader-v2 overlay | Historical near-zero anomaly, not aggregate baseline quality |
| Paper-level QA / Evidence Recall | Not estimated | No headline accuracy or non-inferiority claim is allowed |
| Statistical precision | No disjoint pilot or final runs | No confidence interval, significance, stability, or generalization claim is allowed |

The implementation is therefore precise within its tested contracts, but the
paper's task-quality accuracy is still `NOT_ESTIMATED`.

## 3. M* Terminal Failure

Observed terminal facts:

```text
run_id:                    s5-mstar-20260816-001
status:                    incomplete_non_mergeable
pipeline status:           FAIL_CLOSED
failure stage:             mstar_execution
failure code:              LATEST_STATE_BIND_FAILED
error class:               paper_eval.s5_graphiti_mstar_semantics.
                           S5GraphitiMStarSemanticError
failed source_sequence:    8
intent/prepared:            49/49
commit/publication:         8/49
published prefix:           0..7
configured prepare C:       2
max active prepare:         2
max active bind:            1
fallback count:             0
resume authorized:          false
namespace cleanup allowed:  false
next method authorized:     false
```

The construction server returned HTTP 200 for the failing requests and showed
no context-admission, RoPE, KV-capacity, OOM, or disconnect error. Graphiti
reported four structured JSON parse failures. The final three failed at the
same approximate output position (`char 64121`), after long generations under
the frozen `max_tokens=16384` request budget.

This is strong evidence consistent with completion-cap truncation. It is not
yet proof because the failed attempt did not persist the response
`finish_reason` and per-attempt token usage. The next action must measure those
fields before changing the request envelope.

## 4. Immediate TDD Repair Gate

No unchanged blind retry should be launched. It would consume another
single-use authority without resolving the likely deterministic failure mode.

### R0 - Preserve the failed attempt

Do not:

```text
clean or mutate pev3-s5-mstar-20260816-001
resume or merge the attempt
reuse S5_MSTAR_LIVE_AUTHORITY_20260816.json
run post-observation or synthesize S5_MSTAR_RESULT.json
start S6
```

### R1 - RED tests for sanitized failure evidence

Add tests before production edits for:

```text
1. A semantic substage failure retains a stable public error code:
   resolve_nodes_failed
   extract_edges_failed
   resolve_edges_failed
   extract_attributes_failed
   process_episode_data_failed

2. Every HTTP-complete construction call can be projected to public evidence:
   prompt_name or schema name
   prompt_tokens
   completion_tokens
   total_tokens
   requested_max_tokens
   finish_reason
   HTTP/error class

3. The projection rejects prompt text, response text, episode content,
   credentials, request bodies, and raw provider objects.

4. A controller failure atomically persists the final bounded call envelopes
   before sealing the attempt as incomplete/non-mergeable.

5. Evidence classification is fail-closed:
   finish_reason=length and completion_tokens at budget -> CAP_EXHAUSTED
   finish_reason=stop with invalid JSON              -> STRUCTURED_INVALID
   missing/contradictory telemetry                   -> UNCLASSIFIED
```

The current `QwenVLLMClient` already collects token usage, max tokens, and
finish reason in `call_events`. The minimal implementation should expose a
sanitized snapshot at the S5 controller boundary; it must not wrap or change
the model request itself.

### R2 - Minimal GREEN and parity gate

Implementation constraints:

```text
no parser repair
no raw-output persistence
no extra system prompt
no retry-count change
no max_tokens/context/schema change
no Graphiti algorithm change
no publication-order change
```

Run in order:

```text
focused semantic-error tests
focused telemetry-redaction tests
focused controller failure tests
related S5/M* tests
full paper-eval-v3 offline regression
```

Any touched file in the M* production source closure requires a new source
identity and a fresh current-source FX0 qualification. Historical identities
and the failed run remain immutable.

### R3 - Evidence-driven envelope decision

The decision is deferred until telemetry distinguishes these cases:

| Observation | Allowed next analysis | Forbidden shortcut |
|---|---|---|
| `finish_reason=length`, output at 16,384 | Qualify a larger method-neutral serving envelope and assess whether A0/P*/M* must be rerun under it | Give only M* a larger budget |
| `finish_reason=stop`, invalid JSON | Diagnose vLLM guided-decoding/schema compatibility with one bounded probe | Increase context without evidence |
| Repetitive unbounded array/string generation | Treat as model/schema reliability evidence; inspect upstream schema semantics | Add an arbitrary cardinality bound that changes Graphiti output semantics |
| Missing token/finish evidence | Run only a separately authorized bounded diagnostic | Start another full 49-episode retry blindly |

Changing the completion budget or schema is a material execution-envelope
decision. It requires an explicit amendment, new identities, a new preflight,
and a new single-use authority. It cannot be hidden as an adapter fix.

### R4 - Fresh S5 retry, only after R1-R3

A legitimate retry must use:

```text
new run ID
new namespace with preflight counts nodes=0 and relationships=0
new source/runtime identity
new current-source FX0 qualification when the closure changed
new single-use authority consumed before live I/O
same fixed history 07741c45 and 49 sources
tmux launcher
durable per-event/checkpoint evidence
```

Only a canonical `S5_MSTAR_RESULT.json` with M* PASS authorizes S6 design
activation and live calibration.

## 5. S6 Code Plan After M* PASS

S6 is development-only tuning on the frozen four exposed histories:

```text
07741c45
b6019101
6071bd76
a2f3aa27
```

The fixed matrix is:

```text
P* x C={1,2,4,8} x 4 histories
M* x C={1,2,4,8} x 4 histories
```

No PILOT or FINAL history is available to S6 code.

### Proposed isolated modules

New S6 modules should be additive. Do not weaken the S5 `C=2` authority or
retrofit it into a grid authority.

```text
src/paper_eval/s6_calibration_contract.py
  Pure matrix identity, metric formulas, terminal-state validation.

src/paper_eval/s6_live_authority.py
  S6-specific, one block / one namespace / one concurrency, single-use.

src/paper_eval/s6_pstar_grid.py
  Whole-update runner parameterized only by C in {1,2,4,8}.

src/paper_eval/s6_mstar_grid.py
  Reuse MStarSpec and the qualified semantic core with an S6-specific C.

src/paper_eval/s6_block_controller.py
  One method/history/C block, durable authority consumption, no matrix-wide
  mutable state.

src/paper_eval/s6_block_postprocess.py
  Independent Neo4j observation and direct-invariant binding.

src/paper_eval/s6_matrix.py
  Deterministic 32-block inventory and completed-block discovery.

src/paper_eval/s6_selection.py
  Correctness-first M* qualification and frozen median-goodput selection.

scripts/run_s6_block_tmux.sh
  One detached block per tmux session.

scripts/run_s6_matrix_tmux.sh
  Serial block orchestrator; never overlaps experimental blocks on the shared
  model service.
```

Each production module receives a matching `tests/test_s6_*.py` file. Keep
comments focused on authority, durability, or non-obvious scientific
semantics; routine code should remain self-explanatory.

### S6 metric contract

For each block, derive metrics from verified durable events:

```text
makespan_ns = last_terminal_or_publication_ns - first_service_start_ns

successful_goodput_per_s =
  successful_publication_count / (makespan_ns / 1e9)

freshness_ns(source) =
  publish_timestamp_ns - arrival_timestamp_ns

p95_freshness_ns = frozen deterministic quantile(freshness_ns)
```

Persist, but do not add nested timing values as independent costs:

```text
expected / published / failed / censored
lost / duplicate / fallback
direct hard invariant counts
worker IDs and observed overlap
LLM calls and prompt/completion tokens
embedding calls/text count
DB query/transaction/write counts when passively available
```

The quantile convention and makespan endpoints must be fixed in RED tests
before any live result exists.

### S6 selection contract

For P*:

```text
select C with highest median successful goodput across the four histories
retain every direct violation in the result and main-table input
do not call P* semantics-preserving
```

For M*, first build the qualified set:

```text
all four history blocks terminal and complete
zero direct hard violation
deterministic correctness gate PASS
zero hidden fallback
```

Then:

```text
select qualified C with highest median successful goodput
exact tie -> smaller C
no qualified C -> STOP with mechanism diagnosis
```

Selection reads only finalized block artifacts. It must reject partial runs,
duplicate method/history/C cells, mixed source/runtime identities, missing work
volume, and any PILOT/FINAL ID.

### S6 TDD order

```text
RED 1: fixed four-history x method x concurrency matrix
GREEN 1: pure matrix materializer

RED 2: S6-specific single-use block authority and source closure
GREEN 2: authority verifier/consumer with no live dependencies

RED 3: P* grid scheduling, C=1 overlap exception, C>1 overlap proof,
       terminal failure accounting
GREEN 3: generic S6 P* runner

RED 4: M* grid composition for C={1,2,4,8} without changing S5 C=2
GREEN 4: S6 wrapper around the already grid-capable MStarSpec

RED 5: metric formulas, deterministic p95, work-volume and invariant binding
GREEN 5: pure block result verifier

RED 6: independent Neo4j post-observation and three-way evidence binding
GREEN 6: block postprocessor

RED 7: correctness-first P*/M* selection and exact-tie behavior
GREEN 7: METHOD_SELECTION_FREEZE builder/verifier

RED 8: tmux quoting, environment loading, controller-success -> postprocess
GREEN 8: launchers

focused GREEN
related S5/S6 GREEN
full offline GREEN
read-only service and empty-namespace preflight
one block at a time in tmux
```

### Durable execution layout

```text
artifacts/paper_eval/calibration/
  S6_MATRIX_FREEZE.json
  authorities/
  runs/
    <run-id>/
      authority_consumption.json
      controller/
      attempt/
      post_observation.json
      S6_BLOCK_RESULT.json
  S6_CALIBRATION_RESULT.json
  METHOD_SELECTION_FREEZE.json
```

The matrix orchestrator discovers only canonical `S6_BLOCK_RESULT.json` files.
An SSH/vLLM interruption invalidates only the active block; already finalized
blocks are never rerun or rewritten. Per-source events remain durable for
diagnosis, while scientific aggregation occurs only at complete block
boundaries.

## 6. Stop Boundary

After a valid `METHOD_SELECTION_FREEZE.json` is written and independently
verified, stop before S7. Do not select PILOT IDs, create PILOT namespaces, or
start Reader/Judge quality evaluation automatically.

Current executable conclusion:

```text
S5 M* canonical PASS:  missing
S6 implementation:     design only
S6 live authority:     false
next action:            TDD for sanitized M* failure-envelope evidence,
                        followed by an explicit envelope/retry decision
```
