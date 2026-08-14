# S2 Completion Execution Workplan v1.0

Date: 2026-08-14

Status: frozen bounded execution plan. This plan completes the Native U0 S2
sanity check only. It does not authorize S3, S4, a production baseline freeze,
PILOT, FINAL_PAPER_TEST, construction, cleanup, or policy tuning.

## 1. Fixed question

Can the already-qualified Native U0 graph for one DEVELOPMENT_EXPOSED history
complete a benchmark-unit-aligned retrieval -> Reader -> Judge chain and
produce real Evidence Recall@10 and QA Accuracy values?

The bounded history is:

```text
question_id       07741c45
question_type     knowledge-update
session_count     49
gold_count        2
namespace         pev3-s1-20260814-001
data role         DEVELOPMENT_EXPOSED
```

The history and namespace are reused because S1 already sealed their complete
49/49 U0 construction. No graph construction or mutation belongs to S2
completion.

## 2. Outcome-independent policy

The policy is frozen from benchmark and Graphiti semantics documented in
`S2_COMPLETION_POLICY_RESEARCH_BASIS_20260814.md`, not selected from the R0
numeric score:

```text
policy_id                  graphiti-0.29.3-episode-bm25-session-v1
API                        Graphiti.search_()
native result              EpisodicNode
evaluation result          LongMemEvalSession
query                      exact benchmark question
top-k                      10 unique sessions
candidate limit            20
search/reranker             Episode BM25 / RRF
edge/node/community        disabled
embedding/cross encoder    disabled
filters/temporal filter    none
group scope                exact U0 namespace
custom fusion/dedup        none
```

The freeze must disclose:

```text
r0_outcome_previously_observed            true
selection_not_blinded                     true
r0_numeric_score_used_for_policy_choice   false
candidate_score_search_performed          false
```

No Edge, Node, Community, combined, alternate top-k, prompt, Reader, Judge,
model, parser, or retry candidate will be tried and compared.

## 3. Retrieval and corpus contract

Before search, the runtime must prove an exact one-to-one mapping among the
frozen dataset sessions, projected Episodes, and materialized EpisodicNodes:

```text
49 expected == 49 projected == 49 observed
unique episode names
unique source sequences 0..48
unique session IDs
exact content hashes
exact namespace
```

Missing, duplicate, foreign, or mismatched rows stop before search. Gold IDs
are evaluator-only and cannot enter the query, candidate generation, ranking,
context selection, or presentation order.

Headline metric:

```text
per_question_session_recall_all_at_10 =
  1 iff every gold session is present in the first 10 unique sessions

Evidence Recall@10 = mean(per_question_session_recall_all_at_10)
```

Also persist the binary Recall_any, non-official gold coverage fraction,
retrieved/gold counts, and gold ranks. These diagnostics cannot replace the
headline metric.

## 4. Reader contract

The historical EntityEdge facts Reader is excluded. The formal Reader uses
the pinned LongMemEval flat-session JSON representation:

```text
retriever_type                  flat-session
topk_context                    10
history_format                  json
useronly                        false
cot / con                       false / false
merge_key_expansion_into_value  none
```

The first ten ranked session IDs are mapped to deep-copied original dataset
session values. Every `has_answer` key is removed recursively before rendering.
Selection is rank-first; presentation is then chronological, with equal-date
ties retaining retrieval order. The request contains one user message, no
system prompt, `temperature=0`, `n=1`, `max_tokens=500`, and explicit
`enable_thinking=false`. Context overflow fails closed; truncation is forbidden.

## 5. Judge contract

The Judge reuses the qualified Qwen3 backend and exact vendored LongMemEval
knowledge-update, non-abstention rubric under a new session-chain identity:

```text
one user message; no system prompt
temperature=0; max_tokens=10; n=1
enable_thinking=false
attempts=1; SDK hidden retries=0
headline parser: case-insensitive substring "yes"
audit parser: YES / NO / INVALID
```

The audit parser cannot rewrite the official headline label. `INVALID` or a
service error seals a non-mergeable failure and stops; it is not a QA miss and
is not retried. The backend difference is disclosed as
`PROTOCOL_ALIGNED_NOT_NUMERICALLY_MATCHED`.

## 6. TDD and artifact order

Implementation order is fixed:

```text
RED  session mapping + Recall_any/Recall_all parity
GREEN focused
RED  flat-session materialization + exact prompt + label removal
GREEN focused
RED  Judge parser/rubric identity + synthetic one-chain counters
GREEN focused
RED  policy freeze + qualification + one-shot authorization/consumption
GREEN focused
full offline GREEN
seal S2_COMPLETION_POLICY_FREEZE.json
seal S2_COMPLETION_OFFLINE_QUALIFICATION.json
seal S2_COMPLETION_AUTHORIZATION.json
consume authority before live I/O
run in repository-owned tmux session
seal result or sanitized failure
full offline regression
STOP for result interpretation
```

All pre-live artifacts bind their source/test/JUnit hashes. Raw questions,
answers, sessions, prompts, outputs, endpoints, and credentials remain in
memory only. Durable files store hashes, lengths, counters, public identities,
and numeric metrics.

## 7. Exact live budget

The maximum live budget for one authorized run is:

```text
Graphiti.search_                  1
Neo4j read requests              positive, read-routed only
Reader requests                  1
Judge requests                   1
construction LLM                 0
embedding                        0
cross encoder                    0
database mutations/attempts      0
cleanup                          0
automatic retries               0
```

The authority is consumed before the first database or model call. Any budget,
source, corpus, identity, output-shape, context-envelope, or service failure
produces one sanitized failure artifact, a durable terminal checkpoint, and an
immediate stop. A new attempt requires a new run ID and separate authority.

## 8. Completion interpretation

A successful bounded chain reports real one-question retrieval and QA values.
It is S2 qualification evidence, not a general quality estimate or paper
headline result.

```text
retrieval/Reader/Judge valid and QA=1
  -> eligible to build a full S2 PASS artifact after hash verification

retrieval/Reader/Judge valid and QA=0
  -> record the numeric value, mark reference sanity REVIEW_REQUIRED, and stop

invalid/service/corpus/identity failure
  -> incomplete, non-mergeable, no scientific quality conclusion
```

No branch automatically creates `NATIVE_BASELINE_FREEZE.json`, authorizes S3,
or changes the retrieval policy. Graph-sensitive construction correctness
remains the separate S1/oracle surface and cannot be inferred from Episode QA.
