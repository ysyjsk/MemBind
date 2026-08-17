# QA Accuracy Diagnosis and Remediation

Date: 2026-08-16

Status: development diagnosis completed; held-out data not accessed

## Outcome

The completed Native U0 development run is not losing the annotated evidence:

```text
run_id                         nb-20260816-001
histories                      4 (all knowledge-update)
episodes                       188/188
Evidence Recall@10             4/4
Qwen Reader + Qwen Judge QA    1/4
```

All eight gold sessions are present at ranks 1 or 2. The low QA value therefore
does not establish a Graphiti construction or retrieval failure. The observed
errors are downstream of retrieval:

1. the Reader sometimes selects an obsolete value even though both old and new
   sessions are present and chronologically ordered;
2. the local Qwen Judge can reject semantically acceptable numeric wording,
   such as a response containing "close to 1300" for reference answer `1300`;
3. four development questions, all from one question type, are too few for an
   absolute system-quality estimate.

The current U0 result remains immutable. It is a development diagnostic, not a
paper-significance claim and not an exact reproduction of a published score.

## User-only diagnostic

A read-only development ablation removed assistant turns from the ten retrieved
session values. It did not rebuild Graphiti or alter retrieval:

```text
artifact
artifacts/paper_eval/native_baseline/quality_overlays/
  reader-v3-useronly/nb-20260816-001/QUALITY_OVERLAY_SUMMARY.json

QA                              2/4
Evidence Recall@10              4/4
Reader prompt tokens            20,095
original Reader prompt tokens   115,684
prompt-token reduction          82.63%
```

This result is retained as an exploratory artifact but is rejected as the
common U0/A0/P protocol. LongMemEval section 5.1 says user-only projection is
used when sessions or rounds form retrieval *keys*. It does not say that
Reader session *values* are user-only. The upstream generation CLI defaults
`useronly=false`, and its README recommends that value. A global user-only
Reader would also remove the evidence needed for `single-session-assistant`
questions. Promoting this ablation would create a larger reviewer concern than
the low four-item diagnostic score.

## Closest published implementations

### LongMemEval (ICLR 2025)

The official decomposition is indexing, retrieval, and reading. Retrieved
items are sorted chronologically; the default strong reading recipe is JSON
plus Chain-of-Note. The released generation command defaults to two-sided
session values (`useronly=false`). The paper demonstrates that Reader capacity
is a material part of measured QA:

```text
Qwen2.5-7B, oracle sessions, CoN       50.4%
Qwen2.5-7B, memory K=V                 45.2%
Qwen2.5-7B, memory K=V+fact            46.2%
```

These are full 500-question results under the paper's own retrieval and model
configuration, not expected values for this four-question Qwen3/Graphiti
development slice.

LongMemEval evaluates answers with `gpt-4o-2024-08-06` and question-type
specific prompts. Its meta-evaluation reports 30/30 agreement on sampled
knowledge-update outputs for both GPT-4o and Llama-3.1-8B answer models. The
current local Qwen Judge uses the same rubric text but is a different evaluator
and has no corresponding human-agreement evidence for these outputs.

Sources:

- https://arxiv.org/abs/2410.10813
- https://github.com/xiaowu0162/LongMemEval/tree/9e0b455f4ef0e2ab8f2e582289761153549043fc
- `src/generation/run_generation.py`
- `src/generation/run_generation.sh`
- `src/evaluation/evaluate_qa.py`

### Zep / Graphiti LongMemEval evaluation

The Zep paper's configuration is materially stronger and structurally
different from the current local quality chain:

```text
graph construction             gpt-4o-mini-2024-07-18
answer model                    gpt-4o-mini or gpt-4o
Judge                           GPT-4o + LongMemEval rubric
retrieval context               Graphiti/Zep facts and entities
temporal representation         fact validity date ranges
reported LongMemEval-S QA       63.8% / 71.2%
```

Its paper explicitly notes that less capable models have difficulty
understanding temporal graph data. It also reports a quality regression on
`single-session-assistant`, showing why dropping all assistant turns is not a
safe general fix. The current Zep benchmark code similarly uses a fixed strong
answer model and a separate structured grader rather than using one local model
as construction model, Reader, and Judge.

Graphiti's current repository-level `tests/evals` is not a directly comparable
LongMemEval QA benchmark. It uses the oracle dataset and `gpt-4.1-mini` to
compare graph-building outputs against a baseline. It should not be cited as a
Qwen + Graphiti end-to-end QA result.

Sources:

- https://arxiv.org/abs/2501.13956
- https://github.com/getzep/zep/blob/main/benchmarks/longmemeval/zep_longmem_eval.py
- https://github.com/getzep/graphiti/blob/main/tests/evals/eval_e2e_graph_building.py

## Frozen execution decision

The running three-baseline development suite uses the same already-qualified
Reader-v2 and Qwen Judge for U0, A0, and P(C=2). The runner now extracts the
Reader/Judge identity from every sealed U0 history, compares every A0/P block
against it, and only then emits a common-quality-identity fairness claim. The
local QA number remains diagnostic, while construction performance, Evidence
Recall@10, and cross-method QA parity remain usable.

This avoids two invalid practices:

- rerunning a nondeterministic Reader until the four-item score improves;
- silently changing Reader/Judge only for later baselines.

## Paper-quality remediation

Before PILOT or FINAL, the quality layer should be frozen as two separately
named surfaces:

1. **Evidence retrieval:** keep LongMemEval session Evidence Recall@10 and its
   raw ranked session identities. This isolates whether the required source
   evidence was recalled.
2. **End-to-end memory QA:** use actual Graphiti facts/entities with temporal
   validity metadata as Reader context, a fixed strong Reader, and the official
   LongMemEval GPT-4o Judge or an independently human-qualified replacement.

The same QA adapter, model revisions, prompt hashes, top-k, and Judge must be
used for all methods. Reader/Judge latency must remain outside construction
makespan. Any new adapter is a transparent post-development, pre-pilot protocol
revision and must be selected before held-out results are observed.

Until the official or human-qualified Judge is available, report the present
Qwen label as:

```text
PROTOCOL_RUBRIC_COMPATIBLE_LOCAL_JUDGE_DIAGNOSTIC
```

Do not call it official LongMemEval accuracy and do not compare its absolute
number against Zep, LongMemEval, Mem0, or other published systems.

## TDD and evidence

```text
RED
logs/TDD_RED_BASELINE_FAIRNESS_AND_QA_IDENTITY_20260816.xml
logs/TDD_RED_SMALL_THREE_BASELINE_TMUX_20260816.xml

GREEN
logs/TDD_GREEN_BASELINE_FAIRNESS_AND_QA_IDENTITY_20260816.xml
logs/TDD_GREEN_SMALL_THREE_BASELINE_TMUX_20260816.xml
logs/TDD_RELATED_GREEN_THREE_BASELINE_FAIRNESS_20260816.xml

focused fairness tests          16 passed
tmux launcher tests              3 passed
related offline regression     118 passed
```

The suite runner additionally locks one `suite_run_id` to one immutable U0
source artifact. Restarting with a different U0 source fails closed instead of
combining old A0/P blocks with a new U0 reference.

## Implemented graph-native development overlay (2026-08-17)

The remediation path above is now implemented without modifying the live
baseline runner.  It follows the Zep architectural context shape while binding
every operation to the sealed OSS Graphiti namespace:

```text
top-20 EntityEdge facts + temporal validity fields
top-20 EntityNode names/summaries
one Qwen Reader request with finish_reason required to be stop
one LongMemEval-rubric Qwen Judge request
```

The retriever signature cannot receive a reference answer, gold session IDs,
or raw-session fallback.  Gold session IDs enter only the post-retrieval
coverage calculation, and the reference answer enters only the Judge.  The
runtime uses the operator-attested Qwen3-Embedding-0.6B deployment for cosine
query vectors, prohibits construction LLM and cross-encoder calls, rejects
Neo4j schema initialization, and guards every Neo4j operation as read-only.

Crash recovery is also score-neutral: a completed private/public result bundle
is verified and skipped; a missing public projection is restored from its
sealed private bundle without another model request; a service failure remains
`incomplete_non_mergeable` and receives a new attempt only when the command is
explicitly restarted.  Target discovery additionally requires the sealed
three-baseline final report, and an exclusive run lock prevents two processes
from issuing duplicate Reader/Judge requests for the same overlay run ID.

TDD evidence:

```text
RED
logs/TDD_RED_GRAPHITI_LONGMEMEVAL_QUALITY_20260817.xml
logs/TDD_RED_GRAPH_QUALITY_OVERLAY_EXECUTION_20260817.xml
logs/TDD_RED_GRAPH_QUALITY_LIVE_20260817.xml
logs/TDD_RED_GRAPH_QUALITY_SUITE_20260817.xml
logs/TDD_RED_GRAPH_QUALITY_DISCOVERY_20260817.xml
logs/TDD_RED_GRAPH_QUALITY_RUNNER_20260817.xml

GREEN
logs/TDD_GREEN_GRAPH_QUALITY_COMPLETE_20260817.xml
logs/TDD_GREEN_GRAPH_QUALITY_OVERLAY_EXECUTION_20260817.xml
logs/TDD_GREEN_GRAPH_QUALITY_LIVE_20260817.xml
logs/TDD_GREEN_GRAPH_QUALITY_RUNNER_20260817.xml
logs/TDD_GREEN_GRAPH_QUALITY_FINAL_FOCUSED_20260817.xml

latest focused graph-quality suite: 39 passed
related baseline + graph-quality regression: 115 passed
```

The overlay is queued behind the construction suite in its own tmux session.
It cannot begin until all eight A0/P blocks and the common suite report are
sealed, preventing Reader/Judge traffic from contaminating construction
latency.
