# Native Reader v2 Qualification Result Report

Date: 2026-08-14

Run ID: `native-reader-v2-canary-20260814-001`

Final status: `PASS` for adapter compatibility. This is not a Native Graphiti
quality estimate, is not mergeable into PILOT/FINAL, and does not authorize
pilot execution.

## 1. Decision

The operator proposal was directionally correct: keep Graphiti construction,
Episode retrieval, K=10, dataset, model, and Judge fixed, while moving the
common answering layer to LongMemEval's repository-recommended Reader recipe.
The following claims required correction before implementation:

1. The historical direct Reader was not arbitrary or benchmark-invalid. It was
   a hash-bound port of LongMemEval's supported `direct` flat-session path.
2. The pinned public `READING_METHOD=con` maps to `--cot true` and leaves the
   Python `--con` flag false. It is one Reader completion, not ten note calls
   plus one answer call. The latter is `con-separate` and was not adopted.
3. The change occurred after a direct-Reader failure was observed. It is a
   transparent, non-blind development-stage v2 revision, not preregistration.
4. A development canary QA label cannot qualify system quality. Valid QA=0 and
   QA=1 outcomes both satisfy the same compatibility gate.

The frozen Reader-v2 recipe is:

```text
retriever_type                 flat-session
topk_context                   10
history_format                 json
useronly                       false
reading_method                 con
effective flags                cot=true, con=false
separate note extraction       false
Reader requests/question       1
max_tokens                     800
system prompt                  none
temperature / n                0 / 1
enable_thinking                false
automatic/SDK retries          0 / 0
truncation                     fail closed; observed 0
```

Pinned upstream identities:

```text
repository     xiaowu0162/LongMemEval
commit         9e0b455f4ef0e2ab8f2e582289761153549043fc

run_generation.py SHA256
4f1eb3c69d7ad40f04065b9c0bc86f6582441018fc6ff751d162d66c95baf672

run_generation.sh SHA256
6602147b866eca4a80acdf5e6689389586086216c9198fce7b8380b7495c5422

README.md SHA256
c4ff45676683d9e2f7cf7d9099d26426f14635ec110dbb1da818d1019a142573
```

LongMemEval Section 5.5 and Appendix D support JSON plus a CoN-style prompt as
a stronger reading recipe. They do not guarantee repair of any particular
item, define an 8-16 item qualification requirement, or make `direct` invalid.

## 2. Scope and selection

The historical direct attempt remains immutable:

```text
run_id                   s2-completion-20260814-001
history_id               07741c45
Evidence Recall@10       1.0
QA Accuracy              0.0
status                   REVIEW_REQUIRED
```

Reader-v2 did not rerun that exposed item. The compatibility canary was fixed
before any Reader-v2 outcome using the already-frozen calibration order:

```text
history_id               b6019101
selection                first remaining ID after 07741c45
data role                DEVELOPMENT_EXPOSED
namespace                nc-e1e2-1deef863d4241064
expected sessions        49
```

The namespace is from the sealed C2 run and has a disclosed construction-model
revision difference from current S1 U0. It is suitable for adapter
compatibility because the exact 49-session corpus is hash checked before
retrieval, but its numeric result is not current Native U0 quality evidence.
No graph reconstruction, namespace cleanup, or mutation was performed.

## 3. TDD evidence

Every new execution surface started with a persisted RED collection failure or
contract mismatch before the implementation was added:

```text
Reader semantics and request contract
qualification and outcome-independent selection
one-shot authority and consumption
durable controller and terminal XOR
production wiring
terminal-chain verifier
common-policy freeze
```

Final test state:

```text
Reader-v2 focused suite       74 passed
full paper-eval-v3 suite     451 passed
git diff --check              passed
```

The focused suite covers the exact upstream prompt, `con -> cot=true/con=false`
resolution, max_tokens=800, a single Reader request, label removal, no raw
content persistence, common U0/A0/P*/M* identities, QA-independent
qualification, exact call budgets, exclusive authority consumption, durable
checkpoints, production gold blindness, terminal-chain tamper rejection, and
exclusive freeze finalization.

Final JUnit evidence:

```text
logs/TDD_FOCUSED_GREEN_NATIVE_READER_V2_FINAL_20260814.xml
SHA256 a1074fceebee7d771106cbaf940a4d8d353372dcccc172c49be44591a88f7a6f

logs/TDD_FULL_OFFLINE_GREEN_NATIVE_READER_V2_RECONCILED_FINAL_20260814.xml
SHA256 78e6005a729caa16fb2977ed557f85ca3b0eef16e4681c779cdcda66b707b038
```

## 4. Live result

The authority was consumed before the first Neo4j/model call. The tmux-run
controller completed all checkpoints:

```text
authorization_consumed
retrieval_complete
reader_complete
judge_complete
terminal_success
```

Observed result:

| Surface | Value |
|---|---:|
| Retrieved sessions | 10 |
| Gold sessions | 2 |
| Covered gold sessions | 2 |
| Gold ranks | 1, 2 |
| Recall_any@10 | 1.0 |
| Recall_all@10 | 1.0 |
| Evidence Recall@10 | 1.0 |
| Reader prompt tokens | 26,205 |
| Reader completion tokens | 131 |
| Reader prompt characters | 114,579 |
| Reader output characters | 506 |
| Reader truncations | 0 |
| Judge status | `SUCCESS` |
| Judge parse | `YES` |
| QA diagnostic | 1.0 |

Exact live budget:

```text
Graphiti.search_                  1
Neo4j reads                      2
Reader-v2                        1
Judge                            1
construction LLM                 0
embedding                        0
cross encoder                    0
database mutation/attempt        0 / 0
cleanup                          0
retry                            0
```

There was no service disconnect, context overflow, truncation, invalid Reader
output, invalid Judge output, mutation, or retry.

## 5. Interpretation

The admissible conclusion is:

> On one preselected DEVELOPMENT_EXPOSED C2 graph, the pinned Graphiti Episode
> BM25/RRF retrieval -> LongMemEval session adapter -> repository-recommended
> single-call CoN Reader -> qualified Judge chain completed under its exact
> one-search/one-Reader/one-Judge budget, with zero truncation or retry.

The QA=1 value is deliberately diagnostic. It cannot be compared causally with
the earlier QA=0 because the histories differ, and it was not used to choose,
tune, or freeze the Reader. Reader-v2 would have frozen after any valid QA=0 or
QA=1 result. No claim is made that CoN repaired `07741c45`, that Native Graphiti
has high aggregate QA, or that the canary predicts PILOT/FINAL performance.

The common-policy freeze binds the same Reader and Judge identities for:

```text
U0 == A0 == P* == M*
```

Reader/Judge calls and latency remain a common evaluation layer and must be
reported separately from memory-construction performance. The freeze allows an
S3 configuration update only:

```text
s3_configuration_update_authorized = true
pilot_execution_authorized          = false
s3_authorized                       = false
```

## 6. Artifact index

```text
Workplan
NATIVE_READER_V2_QUALIFICATION_WORKPLAN_v1.0.md
SHA256 26ccc72e7309677336c33b1423581ac7337fc8d78a47cc2acb9cccfac402117c

Contract
artifacts/paper_eval/native/NATIVE_READER_V2_CONTRACT.json
SHA256 54eee5e9748c4915d226aa7da35c9e793d3a444baff60994c83657c83e0e4e27

Offline qualification
artifacts/paper_eval/native/NATIVE_READER_V2_OFFLINE_QUALIFICATION.json
SHA256 b767a63095c55c23036ea8dc597a42e71621abae7050c1046a2914bf2493f2c3

One-shot authorization
artifacts/paper_eval/native/NATIVE_READER_V2_AUTHORIZATION.json
SHA256 3908ca4fda58b1db77b10cccb62fc7495682ebecf51d5c9b9d2024b86c8ebd53

Authorization consumption
artifacts/paper_eval/native/runs/native-reader-v2-canary-20260814-001/
NATIVE_READER_V2_AUTHORIZATION_CONSUMPTION.json
SHA256 47f464b1fe0f3ba33c2d86a43fde9942008404122bda5258369f7d85b9b9eeb9

Events
artifacts/paper_eval/native/runs/native-reader-v2-canary-20260814-001/events.jsonl
SHA256 30bcc93a42b6cdfac8fced4bf15044699199b130bf657dc33310e65f5577492f

Checkpoint
artifacts/paper_eval/native/runs/native-reader-v2-canary-20260814-001/checkpoint.json
SHA256 b924c8ee32f247f7aaf911403d7d8b3532269ec1c8425707be69be32a7ca0d62

Result
artifacts/paper_eval/native/runs/native-reader-v2-canary-20260814-001/
NATIVE_READER_V2_RESULT.json
SHA256 f58dd05a4b2b3185811a76d52efe99f70ffdf2558c2a9e6630498d1f19e788f3

Common-policy freeze
artifacts/paper_eval/native/NATIVE_READER_V2_FREEZE.json
SHA256 2ca01fd37b4c949ba226254d58aed64ae0fa345d0d300efa9bd8327efd6d93f7

Live console
logs/NATIVE_READER_V2_LIVE_20260814.log
SHA256 d7691def84c8aeec036890b82f88226e136e8fb4d75972d0a9a5f41ab1b30396
```

Historical direct artifacts were not modified. No PILOT/FINAL role or method
result was inspected or authorized by this stage.
