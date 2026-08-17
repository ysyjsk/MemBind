# QA Decomposition Result Report

Date: 2026-08-17

Status: `PASS` — bounded development diagnostic; no held-out data accessed

## Outcome

The read-only QA decomposition completed all eight predeclared units over the
four sealed Native U0 namespaces. Graphiti construction was not rerun or
mutated. Each history reused the same Qwen3-32B-FP8 Reader and Qwen
LongMemEval-rubric Judge under two context variants:

```text
top10      ten Episode-BM25/RRF sessions, chronologically presented
gold_only  exactly the two annotated answer sessions, chronologically presented
```

Headline development result:

| Variant | QA | Reader prompt tokens | Interpretation |
| --- | ---: | ---: | --- |
| `top10` | 1/4 = 0.250 | 115,223 | Reproduces the existing low-QA pattern |
| `gold_only` | 3/4 = 0.750 | 23,305 | Qwen can answer most items when noise is removed |
| Difference | +0.500 | -79.77% | Strong context-usability signal |

The result does not establish a full-benchmark accuracy value. All four items
are exposed development questions of type `knowledge-update`.

## Per-question diagnosis

| History | Top-10 | Gold-only | Source-level diagnosis |
| --- | ---: | ---: | --- |
| `07741c45` | 0 | 1 | Top-10 Reader selects the obsolete `under bed` state. Gold-only sees the closet/shoe-rack update, but its final answer remains internally contradictory (`under bed`, planning to move). The Qwen Judge accepts it because the required target phrase appears; this is a potential false-positive under a strict current-state reading. |
| `b6019101` | 0 | 1 | Top-10 selects an irrelevant/stale count of 4 MCU films. Gold-only directly recovers 5 and answers correctly. This is clear context selection/noise failure. |
| `6071bd76` | 1 | 1 | Both contexts recover the transition from 6 to 5 ounces and correctly conclude less water. Reader and Judge agree. |
| `a2f3aa27` | 0 | 0 | Gold-only sees 1250 and the later statement `close to 1300`, but chooses the last exact count 1250 instead of the benchmark reference 1300. This is a Reader/rubric interpretation failure even with oracle sessions. |

The newly generated `a2f3aa27/top10` answer also consumed exactly 800 output
tokens and ended before a final answer. The frozen Reader budget is 800, so
this unit is marked as output-cap saturation evidence. It must not be used to
claim that the original Native U0 response was truncated: the original U0
generation was a different nondeterministic sample and used 401 completion
tokens.

## Reader versus Judge conclusion

The experiment does not support the hypothesis that the low `0.25` score is
primarily caused by Qwen Judge false negatives:

```text
clear Reader/context failures corrected by gold-only   2/4
already correct in both contexts                       1/4
Reader/rubric ambiguity remaining under gold-only      1/4
observed clear Judge false negative                     0
potential Judge false positive                         1 (07741c45 gold-only)
```

The persisted Qwen-rubric headline remains `gold_only=0.75`; the potential
false-positive is an audit annotation, not a post-hoc score rewrite. A human
label or GPT-4o sensitivity pass over the already fixed answers can resolve it
without another Reader call or any construction rerun.

## Implication for the paper evaluation

1. Qwen3-32B non-thinking is demonstrably capable on this local workload; it
   answers three of four items under the official-rubric oracle-session lane.
2. Session Evidence Recall@10 remains a source-session availability metric, not
   an end-to-end context-usability guarantee.
3. The current low main QA is dominated by Reader context selection and
   temporal/current-state interpretation, not proven Graphiti ingestion loss.
4. The graph-native overlay remains a separate retrieval problem: its edge
   source coverage is only 0.458 overall, so its `0/12` must not be combined
   with this raw-session diagnosis.
5. Any final official-quality table should reuse fixed Reader answers and run a
   strong, paper-comparable Judge post hoc. Judge latency remains outside the
   construction makespan for every method.

## TDD and recovery evidence

The implementation was test-first:

```text
RED  module import failed before qa_decomposition.py existed
RED  live module import failed before resumable execution existed
GREEN focused contracts          14 passed
GREEN related Reader/Judge suite 53 passed
```

The live runner seals `reader_stage.json` before the Judge request. Tests prove
that a Judge disconnect causes a restart to reuse the Reader answer and issue
only the missing Judge call. A fully completed unit reuses both stages. Raw
questions, prompts, answers, references, session IDs, and Judge output live
only in ignored private paths; public artifacts contain hashes and counts.

## Evidence locations

```text
Main result
artifacts/paper_eval/qa_decomposition/runs/
  qd-dev-20260817-001/QA_DECOMPOSITION_RESULTS.json

Per-unit public results
artifacts/paper_eval/qa_decomposition/runs/
  qd-dev-20260817-001/<history>/<variant>/public.json

Local private audit and resumable stages (gitignored)
artifacts/paper_eval/qa_decomposition/runs/
  qd-dev-20260817-001/<history>/<variant>/private_bundle.json
  qd-dev-20260817-001/<history>/<variant>/runtime/private/*.json

Live log
logs/QA_DECOMPOSITION_LIVE_20260817.log

TDD logs
logs/TDD_GREEN_QA_DECOMPOSITION_FOCUSED_20260817.xml
logs/TDD_GREEN_QA_DECOMPOSITION_RELATED_20260817.xml
```

Authoritative result payload SHA-256:

```text
393746d5fa1343658a3bf125859e84e13fd8418271caa7a6313a48e7cea88353
```
