# S2 Completion Policy Research Basis

Date: 2026-08-14

Status: research-only, non-authoritative design basis.

This document is not a protocol amendment, policy freeze, live authorization,
stage transition, experiment result, or S3 authorization. It does not alter any
completed S1/S2/S2-R0 artifact. A future S2-completion protocol must restate,
test, hash-bind, and explicitly authorize any adopted decision before live I/O.

## 1. Outcome-independent decision

The smallest executable formal retrieval policy should be Graphiti 0.29.3
Episode-only BM25 with RRF, evaluated as a ranked LongMemEval session list:

```text
policy_id                  graphiti-0.29.3-episode-bm25-session-v1
retrieval_method           Graphiti.search_()
native_result_type         EpisodicNode
evaluation_result_unit     LongMemEvalSession
top_k_unit                 unique_session
query                      exact benchmark question
top_k                      10
candidate_limit            20 (Graphiti internal 2 * limit)
episode search             BM25 only
episode reranker           RRF
reranker_min_score         0
edge/node/community        disabled
query embedding            none
cross encoder              none
SearchFilters              empty
group_ids                  exactly one history namespace
question_date in retrieval false
temporal filter            none
custom fusion/sort/dedup   none
```

Every `EpisodicNode` must map one-to-one through the frozen
`episode name/source_sequence -> session_id` map. Missing, duplicate, foreign,
or content-hash-mismatched entries are corpus qualification failures. They must
not be silently skipped or deduplicated.

This selection follows benchmark and architecture constraints rather than a
best observed score:

1. LongMemEval evidence labels and official retrieval metrics use sessions.
2. EpisodicNode is the only native Graphiti search result with a direct
   one-session-per-result mapping in the project's frozen ingestion contract.
3. Edge, node, community, and combined results do not define one unified
   session-ranked top 10. Project-side projection or fusion would recreate the
   historical Edge@10 unit mismatch or introduce a new retrieval algorithm.
4. The policy can be held identical for U0, A0, P*, and M*.

Episode retrieval is not a whole-graph quality measure. Graph parity,
provenance, temporal/publication invariants, lost/duplicate updates, and direct
violations remain an independent construction-correctness surface.

## 2. Honest R0 disclosure

The successful R0 outcome was observed before this research basis was written.
The future freeze must not claim blinded or preregistered selection. It should
state:

```text
r0_outcome_previously_observed       = true
selection_not_blinded                = true
r0_numeric_score_used_for_choice     = false
candidate_score_search_performed     = false
selection_basis                      =
  BENCHMARK_RESULT_UNIT_ALIGNMENT
  + UPSTREAM_NATIVE_API
  + NO_CUSTOM_CROSS_SURFACE_FUSION
  + SAME_POLICY_FOR_ALL_METHODS
```

The freeze builder should reject candidate-surface scores, best-of-grid
language, R0 thresholds, and tuning records as selection evidence. The R0 run
may remain a DEVELOPMENT_EXPOSED diagnostic, but it cannot enter PILOT or
FINAL_PAPER_TEST results. PILOT and FINAL policies must be frozen before their
disjoint outcomes are observed.

## 3. Evidence Recall@10

The parent protocol's `Evidence R@10` should be made unambiguous:

```text
per_question_session_recall_all_at_10 =
  1 iff every answer_session_id occurs in the first 10 ranked unique sessions

aggregate_evidence_recall_at_10 =
  mean(per_question_session_recall_all_at_10)
```

Also report, without replacing the headline metric:

```text
session_recall_any_at_10
session_gold_coverage_fraction_at_10  # explicitly non-official diagnostic
retrieved_session_count
gold_session_count
gold ranks
```

`answer_session_ids` are evaluator-only inputs. They must never affect the
query, candidate set, ranking, Reader context selection, or ordering.

The metric semantics match LongMemEval's binary `recall_any` and `recall_all`.
The retriever implementation does not: it is Graphiti Episode full-text search,
not an official LongMemEval retriever. Both facts must be disclosed together.

## 4. Official flat-session Reader semantics

The Reader should materialize the ranked session IDs using the frozen dataset's
original `haystack_sessions`, matching LongMemEval flat-session value lookup:

```text
retriever_type                  flat-session
topk_context                    10
history_format                  json
useronly                        false
cot                             false
con                             false
merge_key_expansion_into_value  none
```

Required pipeline:

1. Take the first ten validated retrieved session IDs in native rank order.
2. Deep-copy their original `[{role, content}, ...]` values and remove
   `has_answer`; the label must never reach the Reader.
3. Sort selected sessions chronologically for prompt presentation, as upstream
   LongMemEval does. Equal dates retain retrieval-relative order.
4. Render the official ordinary chat-history template beginning `I will give
   you several history chats`, not the facts/replace template.
5. Include the benchmark `question_date` as Current Date and the exact question.
6. Send one user message, no system prompt, temperature 0, n=1,
   max_tokens=500, and effective thinking disabled.
7. Prefer a frozen fail-closed context-envelope guard with zero truncations.
   This is a disclosed stricter local behavior than upstream token truncation.

Database episode content must pass the frozen name/session/content-hash guard
before dataset value materialization, so the Reader adapter cannot conceal a
corrupt or incomplete constructed corpus.

## 5. Judge semantics

Use the exact vendored LongMemEval `knowledge-update`, non-abstention rubric.
The fixed request should be one user message, no system prompt, temperature 0,
max_tokens 10, n=1, effective thinking disabled, zero SDK hidden retries, and
one attempt.

Headline parsing remains upstream-compatible:

```python
qa_label_official = "yes" in raw_output.lower()
```

A parallel strict audit parser records `YES`, `NO`, or `INVALID`, but must not
silently rewrite the official label. S2 qualification requires zero invalid
outputs; malformed output is an incomplete qualification attempt, not a QA
miss and not an automatic retry. The knowledge-update rubric counts an answer
as correct when it includes previous information plus the required updated
answer.

The qualified local Judge is Qwen3-32B-FP8 rather than an official LongMemEval
reference backend. Therefore the admissible claim is
`PROTOCOL_ALIGNED_NOT_NUMERICALLY_MATCHED`, with the backend difference and
full deployment/request identity disclosed.

## 6. Reuse boundary

Reusable after new identity binding:

- OpenAI-compatible transport and sanitized response envelopes;
- generic append-only durability/checkpoint machinery;
- vendored official rubric and qualified Judge backend;
- frozen dataset/session mapping and corpus completeness guards;
- pure official-metric helpers after parity tests.

Not reusable as the formal session Reader chain:

- `RetrievedFact`, `EntityEdge.fact`, and the facts/replace prompt in the
  existing `s2_reader.py`;
- historical edge-ranked top 10 and post-ranking provenance projection;
- the edge-derived `U0_REFERENCE_SANITY.json` QA and retrieval numbers;
- edge UUID/provenance materialization in the historical `s2_live` path;
- the old adapter identity, which binds `graphiti_basic_edge`;
- the R0 diagnostic contract as if it were already a formal policy freeze.

Existing sealed artifacts remain immutable. A formal session policy requires a
new versioned contract and a separate authorization chain.

## 7. Minimum TDD order

```text
RED: reject edge/mixed result units and R0-score-based policy selection
RED: reject duplicate/missing/foreign episode-to-session mappings
RED: exact official flat-session prompt golden fixtures
RED: prove has_answer never reaches the Reader
RED: Recall_any/Recall_all parity fixtures
RED: exact Judge rubric hashes and official/strict parser separation
RED: synthetic retrieval -> materialization -> Reader -> Judge call counters
GREEN: focused suite
GREEN: full offline suite
seal a separate S2_COMPLETION_POLICY_FREEZE.json
seal and consume one-shot live authorization
run one bounded DEVELOPMENT_EXPOSED qualification chain
seal result or sanitized failure; do not tune the policy on failure
```

An oracle-Reader call, alternate surface, extra retrieval, automatic retry, S3
freeze, or stage transition requires separate authority and is not implied by
this document.

## 8. Sources and identities

- Parent protocol:
  `MemBind_PAPER_EVALUATION_WORKPLAN_v3_SMALL_FIRST_FINAL.md`, SHA256
  `4b81c89b33d407f04fc20862a81eab6badba16d0d61d98331cbe188d1bb4f41e`.
- Retrieval amendment v1.1, SHA256
  `71c66ea293406eb155727f1a55d6fc7b618b5fbb2ea16aa9e5dc9ec4e9ea0ddd`.
- LongMemEval, ICLR 2025, arXiv:2410.10813; official repository
  `xiaowu0162/LongMemEval`, commit
  `9e0b455f4ef0e2ab8f2e582289761153549043fc`: `run_retrieval.py`,
  `retrieval/eval_utils.py`, `run_generation.py`, and `evaluate_qa.py`.
- Graphiti/Zep, arXiv:2501.13956; Graphiti repository commit
  `021d3a57d511f21b10adaf7fa923bd5c1fce5e9d`, project version 0.29.3.
  Pinned source hashes: `graphiti.py` `7c65051a...`, `search.py`
  `d0ebae21...`, `search_config.py` `43bbc500...`, and
  `search_config_recipes.py` `e06aae46...`.
- Mnemis, arXiv:2602.15313, repository commit
  `4552fed19bc0cde7b990a6ceb0365cd75b1b3453`. It supports the contextual
  claim that Episode and structured graph representations can be
  complementary; it does not define this retrieval policy or a graph-parity
  correctness gate.
- vLLM, SOSP 2023, arXiv:2309.06180, and DistServe, OSDI 2024,
  arXiv:2401.09670, support holding output semantics/quality constraints fixed
  while comparing systems performance. They do not define the benchmark
  metrics above.

No live service, model, embedding, database, construction, Reader, or Judge
request was performed to produce this research basis.
