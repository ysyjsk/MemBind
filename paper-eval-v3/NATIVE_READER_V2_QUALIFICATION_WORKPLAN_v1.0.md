# Native Reader v2 Qualification Workplan v1.0

Date: 2026-08-14

Status: active, bounded amendment to paper-eval-v3 S2/S3 only. Historical S2
artifacts remain immutable. This document does not authorize PILOT,
FINAL_PAPER_TEST, a retrieval sweep, graph reconstruction, or MemBind method
execution.

## 1. Decision and disclosure

The completed direct-Reader attempt is retained unchanged:

```text
run_id                   s2-completion-20260814-001
history_id               07741c45
data role                DEVELOPMENT_EXPOSED
Evidence Recall@10       1.0
QA Accuracy              0.0
status                   REVIEW_REQUIRED / non-mergeable as full S2 PASS
observed failure         Reader selected the stale prior state
```

That attempt used a hash-bound port of LongMemEval's supported `direct`
flat-session path. It was not an arbitrary or benchmark-invalid Reader. After
observing its failure, this amendment adopts the pinned LongMemEval repository's
recommended single-call reading recipe:

```text
history_format           json
useronly                 false
reading_method           con
effective Python flags   cot=true, con=false
max_tokens               800
```

The change is therefore both upstream-grounded and non-blind. Every artifact
must truthfully record:

```text
prior_direct_failure_observed             true
reader_v2_selection_not_blinded           true
change_motivated_by_observed_failure       true
recipe_source                              upstream_recommended
direct_path_was_officially_supported       true
retrieval_or_top_k_candidate_search        false
```

No document may describe the change as preregistered, as fixing an invalid
benchmark contract, or as guaranteed to repair the exposed item.

## 2. Exact upstream semantics

Pinned sources:

```text
repository       xiaowu0162/LongMemEval
commit           9e0b455f4ef0e2ab8f2e582289761153549043fc

src/generation/run_generation.py
SHA256           4f1eb3c69d7ad40f04065b9c0bc86f6582441018fc6ff751d162d66c95baf672

src/generation/run_generation.sh
SHA256           6602147b866eca4a80acdf5e6689389586086216c9198fce7b8380b7495c5422

README.md
SHA256           c4ff45676683d9e2f7cf7d9099d26426f14635ec110dbb1da818d1019a142573
```

The shell entry point resolves public reading-method names as follows:

```text
direct        -> --cot false
con           -> --cot true
con-separate  -> --cot true --con true
```

This amendment selects `con`, not `con-separate`. It therefore issues one
Reader completion containing the upstream step-by-step extraction/reasoning
prompt. It must not issue one note-extraction call per session. The common
misreading of the Python `--con` flag is an implementation-blocking error.

The local port intentionally differs from the upstream runner only where the
pinned Qwen3/vLLM environment requires an explicit, safer execution envelope:

```text
model                       qwen3-32b-fp8
messages                    one user message, no system prompt
temperature / n             0 / 1
max_tokens                  800
enable_thinking             false, explicitly sent
attempts                    1
SDK hidden retries          0
context policy              fail closed, no truncation
raw durable content         forbidden
```

Upstream supports local OpenAI-compatible serving, but its pinned model table
does not include Qwen3 and its helper retries `RateLimitError`. The local port
may therefore claim exact prompt/materialization semantics, not byte-for-byte
execution of the unmodified upstream program.

## 3. Frozen unchanged surface

Reader v2 changes only the final answer prompt and completion cap. The following
remain fixed:

```text
Graphiti                    0.29.3 / pinned commit
construction               upstream U0 add_episode semantics
retrieval API               Graphiti.search_()
retrieval unit              EpisodicNode -> LongMemEval session
retrieval recipe            Episode BM25 / RRF
query                       exact benchmark question
top-k                       10 unique sessions
candidate limit             20
session materialization     original haystack_sessions values
label removal               recursive has_answer removal
presentation order          chronological after rank-first top-k selection
history format              JSON
user/assistant turns        both retained
Judge                       pinned LongMemEval knowledge-update rubric
dataset/workload            unchanged
```

Forbidden during this amendment:

```text
alternate retrieval surfaces
top-k or candidate-limit sweep
prompt candidates
direct-vs-con score selection
con-separate
model replacement
Graphiti tuning
new benchmark or baseline
PILOT or FINAL IDs
construction or namespace cleanup
```

## 4. What qualification means

This stage qualifies an evaluation-layer implementation; it does not estimate
Native Graphiti quality. A one-item correctness threshold is statistically and
methodologically inappropriate, especially after that item's direct outcome
has been observed.

The live compatibility canary is fixed before observing any Reader-v2 outcome:

```text
history_id            b6019101
selection rule        first remaining ID after 07741c45 in the already-frozen
                      calibration order
data role             DEVELOPMENT_EXPOSED
existing namespace    nc-e1e2-1deef863d4241064
expected sessions     49
```

This item differs from the exposed direct-Reader failure. Its existing graph
comes from the sealed C2 run and has a disclosed construction-model revision
drift relative to the current S1 U0 envelope. It is therefore usable only to
qualify the retrieval -> Reader-v2 -> Judge adapter. Its numbers cannot be
reported as current Native U0 quality or merged into PILOT/FINAL. If the exact
49-session corpus is not still present, the run stops before any model call;
this amendment does not authorize reconstruction.

The old `07741c45` item remains an offline regression/root-cause fixture and is
not rerun live for Reader-v2 selection. The canary's Reader-v2 QA label is
diagnostic only:

```text
QA=1  does not prove CoN repair or Native quality
QA=0  does not invalidate the official recipe or trigger tuning
```

Qualification PASS requires only:

```text
exact frozen corpus mapping
one unchanged Graphiti retrieval
ten unique materialized sessions
one valid Reader-v2 request/response
one valid Judge request/response
zero label leakage
zero truncation
zero retries
zero construction/embedding/cross-encoder/mutation/cleanup calls
complete sanitized checkpoints and terminal artifact
```

An invalid response, service disconnect, context/KV/RoPE/OOM error, corpus
drift, budget violation, or artifact verification failure produces an
incomplete/non-mergeable failure and immediate STOP. No automatic retry or
fallback is allowed.

The proposed 8-16 development examples are not adopted here. They are not a
published qualification standard, would not support a formal accuracy claim,
and would require unnecessary construction or reuse of drifted graphs. Quality
is measured later on the already planned, disjoint PILOT with Reader v2 fixed
identically for U0, A0, P*, and M*.

## 5. TDD order

Implementation is additive. Historical direct-Reader modules and artifacts are
not rewritten.

```text
RED  public reading-method resolver maps con -> cot=true/con=false
RED  exact upstream CoN answer-prompt golden fixture
RED  JSON, USERONLY=false, chronological ordering, recursive label removal
RED  one Reader call, max_tokens=800, no system prompt, thinking disabled
RED  reject con-separate and any extra note-extraction call
RED  safe result projection contains hashes/counters, no raw content
RED  common Reader-v2 identity is byte-identical across U0/A0/P*/M*
RED  qualification semantics are independent of the canary QA label
RED  one-shot authority, consumption, checkpoint, terminal XOR, no retry
GREEN focused Reader/contract/controller suite
GREEN full paper-eval-v3 offline suite
git diff --check
seal source/contract/identity/qualification/authorization artifacts
consume authority before the first live read/model call
run once in repository-owned tmux
verify terminal artifact
GREEN focused post-live
GREEN full offline post-live
```

RED evidence must show a meaningful missing-contract failure before the
implementation exists. Test reports are written under `logs/` and their hashes
are bound into the offline qualification.

## 6. Exact live budget and durability

For the single, preselected exposed compatibility canary `b6019101`:

```text
Graphiti.search_                  1
Neo4j read requests              positive, read-routed only
Reader-v2 requests               1
Judge requests                   1
construction LLM                 0
embedding                        0
cross encoder                    0
database mutation/attempt        0 / 0
cleanup                          0
automatic retry                  0
```

The controller appends sanitized stage events and atomically replaces a
checkpoint after:

```text
authorization_consumed
retrieval_complete
reader_complete
judge_complete
terminal
```

Long-running execution uses tmux. A repeated launcher with the same run ID must
refuse a second controller; a consumed authority is never reusable.

## 7. Freeze and continuation rule

On verified compatibility PASS, create a new Reader-v2 freeze rather than
rewriting any direct-Reader file:

```text
NATIVE_READER_V2_FREEZE.json
```

It binds the upstream sources, local source/tests, model/runtime request
identity, unchanged retrieval contract, Judge identity, historical direct
result hash, qualification result hash, and these method bindings:

```text
U0 == A0 == P* == M* Reader-v2 identity
U0 == A0 == P* == M* Judge identity
```

The freeze authorizes only updating the S3 Native baseline configuration to
reference Reader v2. It does not authorize PILOT or infer a quality result.
Subsequent quality comparisons must use Reader v2 unchanged for every method,
and Reader/Judge latency and token work are reported separately from memory
construction performance.

If compatibility fails, preserve the terminal failure and STOP. Changing the
prompt, K, model, retry policy, ordering, retrieval, or completion cap requires
a new explicit plan; it is not an automatic branch.

## 8. Parent evidence

```text
parent paper workplan SHA256
4b81c89b33d407f04fc20862a81eab6badba16d0d61d98331cbe188d1bb4f41e

historical S2 completion workplan SHA256
778102e6a5d85a559ed3d20d8e236fce25ac9ec141830bcd7399eb1555920ca6

historical S2 result report SHA256
f981e23cbbc239cc0252fde4a92c8c08f9f605cb6be5936bff9363250d810f82

operator opinion attachment SHA256
03979da7c1fa04857a071d4359f413381c906ec56ba847da54b7f09fc34de8b4
```

Research basis: LongMemEval, ICLR 2025, arXiv:2410.10813, especially the
indexing/retrieval/reading decomposition and Section 5.5/Appendix D Reader
study. The paper supports JSON plus CoN as a reading recipe; it does not make a
single exposed answer a valid quality gate and does not require an 8-16 item
qualification sample.

<!-- Maintenance note: this is a narrow versioned amendment. Add new research
questions or evaluation stages in a separate plan; do not grow this file into
a second main protocol. -->
