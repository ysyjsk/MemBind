# MemBind Paper Evaluation Protocol Amendment v3.1

Date: 2026-08-14  
Status: frozen interpretation and execution overlay  
Parent protocol: `（主实验）MemBind_PAPER_EVALUATION_WORKPLAN_v3_SMALL_FIRST_FINAL.md`  
Parent protocol SHA256: `4b81c89b33d407f04fc20862a81eab6badba16d0d61d98331cbe188d1bb4f41e`

This amendment does not rewrite the parent protocol or any completed S0-S2 artifact. It corrects the retrieval-unit contract exposed by the stopped S2 run and freezes the smallest admissible recovery path. Historical S2 remains incomplete/non-mergeable. S3 remains unauthorized until S2-R0 is sealed and the evaluation policy is frozen offline.

## 1. Claim Boundary

MemBind changes incremental construction scheduling, not the retrieval algorithm. A paper-level success claim therefore requires all three independent conditions:

1. Benchmark utility: frozen session-aligned `Recall_all@10` and QA Accuracy stay within preregistered tolerances.
2. Construction correctness: canonical/semantic graph checks pass and `Direct Violations = 0` for provenance, temporal, publication-order, duplicate, and lost-update invariants.
3. Systems benefit: successful-construction goodput increases while P95 freshness and makespan improve under the same quality/correctness gate.

No one surface substitutes for another. Episode retrieval cannot prove graph correctness, and graph parity cannot prove downstream QA utility.

## 2. Retrieval Surfaces

### Native basic edge diagnostic

`Graphiti.search()` in pinned Graphiti 0.29.3 returns `EntityEdge` rows. Its top-k unit is an edge. Projecting those rows back to source sessions yields only `Edge-attributed Source-session Coverage@10`; it is not LongMemEval Session Recall@10.

The historical S2 zero is consequently limited to `GOLD_EPISODES_HAVE_NO_ENTITYEDGE_PROVENANCE`. It does not establish reader failure, whole-memory failure, or absence of answer-bearing episodic content.

### S2-R0 session-aligned episode diagnostic

S2-R0 uses one `EpisodicNode` per frozen haystack session and returns top-10 unique sessions. Its metric unit matches LongMemEval session evidence, so it may compute binary `Recall_any@10`, binary `Recall_all@10`, and fractional gold-session coverage.

The retriever is Graphiti 0.29.3 episode full-text with one-list RRF. It is not the official LongMemEval retriever implementation. The artifact must record both facts explicitly:

```text
official_longmemeval_session_metric = true
official_longmemeval_retriever_implementation = false
retriever_implementation_identity = graphiti-0.29.3-episode-fulltext
```

EntityNode, CommunityNode, and multi-surface retrieval remain untested by S2-R0.

### Graph-sensitive correctness

Formal method comparison must independently inspect canonical and semantic graph parity, entity/fact preservation, source provenance, temporal validity/invalidation, source-order publication, duplicates, losses, and direct invariant violations. Episode-BM25 QA is never a replacement for this lane.

## 3. Fail-Closed S2-R0 Guard

Before the only allowed read-only probe:

```text
input session IDs == frozen haystack session IDs
corpus mapping hash == frozen corpus mapping hash
observed Neo4j EpisodicNode names == expected episode names
gold answer_session_ids subset of frozen session IDs
episode names unique
session IDs unique
one EpisodicNode per session
```

Any mismatch stops before `Graphiti.search_()`. A corpus/indexing failure must not be recorded as a retrieval miss.

The search config is built fresh per call. This avoids Graphiti's module-level mutable search-recipe object and fixes the exact scope to episode-only BM25, RRF, threshold 0, and top-k 10. Source inspection confirms that this config does not request an embedding and does not invoke the cross-encoder.

S2-R0 performs no construction, namespace mutation, reader, or judge work. It records one explicit corpus-read API boundary plus one Graphiti search API boundary. Underlying database wire-request count is `unknown` unless separately instrumented; it must not be falsely recorded as zero.

## 4. Result Interpretation

```text
Recall_all@10 = 1
  -> gold sessions are reachable on the tested Episode surface;
     seal result, stop, and freeze evaluation policy offline.

Recall_any@10 = 1 and Recall_all@10 = 0
  -> partial episode reachability;
     stop for offline diagnosis.

Recall_any@10 = 0
  -> Edge and Episode surfaces are near-zero;
     stop; node and multi-surface paths remain untested.

corpus/config/hash/mapping failure
  -> no retrieval conclusion; return to the affected qualification step only.
```

No result selects the final retrieval policy by score. The policy is frozen from benchmark-unit alignment, pinned upstream implementation identity, and a predeclared method before later method-specific outcomes are inspected.

## 5. Formal Evaluation Gate

All U0/A0/P*/M* comparisons share histories, arrival schedule, Graphiti, construction model, embedder, database, reader, judge, retrieval unit/policy, top-k, timeouts, retries, resources, and performance definitions. Only the construction/scheduling mechanism may vary.

Primary quality fields are named explicitly:

```text
QA Accuracy
Session Recall_all@10
Direct Violations
```

Primary systems fields are:

```text
P95 Freshness
Successful-construction Goodput
Makespan
```

Tolerances and statistical sample planning must be frozen before final outcomes. Better latency or goodput cannot rescue a method that fails the frozen utility or graph-correctness gate.

## 6. Ordered Execution

1. Complete the corpus-completeness and diagnosis-scope RED tests.
2. Implement the minimum pure policy and reach focused GREEN.
3. Run the full isolated-lane offline regression.
4. Bind current source hashes in a new amendment artifact; do not edit historical artifacts.
5. Explicitly authorize exactly one read-only S2-R0 against the immutable S1 namespace.
6. Seal its counters, identities, mapping hash, ranked session hashes, and result.
7. Stop and freeze the evaluation policy offline.
8. Requalify S3 only after all frozen identities and graph-correctness gates are complete.

No construction rerun, namespace cleanup, reader/judge call, retrieval sweep, or new live diagnostic is authorized by this document alone.
