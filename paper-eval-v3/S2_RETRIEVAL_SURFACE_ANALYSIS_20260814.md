# S2 Retrieval-Surface Analysis and Design Correction

Date: 2026-08-14

Scope: `paper-eval-v3` only. The completed S1 namespace, the historical S2
attempt, legacy C0-C5 evidence, and the parent frozen workplan are not mutated.

## Executive decision

The S2 result is not a service failure and is not an official LongMemEval
session-level Recall@10 measurement.

The completed run executed the following pipeline:

```text
Graphiti.search(query, num_results=10)
  -> 10 ranked EntityEdge objects
  -> edge episode provenance
  -> 6 distinct source-session IDs
  -> Reader over 10 edge-fact strings
  -> Judge
```

The two gold episodes were both published and had entity mentions, but neither
had EntityEdge provenance. Because Graphiti's basic `search()` returns only
EntityEdge objects, those gold episodes were structurally unreachable through
the executed surface.

The strongest supported classification is therefore:

```text
GOLD_EPISODES_HAVE_NO_ENTITYEDGE_PROVENANCE
```

It is not:

```text
Graphiti whole-memory quality is zero
the construction service failed
the Reader failed
the Judge failed
official LongMemEval Recall@10 is zero
```

S3 remains unauthorized. No additional retrieval, Reader, Judge, construction,
cleanup, or namespace mutation was performed during this review.

## Evidence from the project

The historical run is `s2-live-20260814-001` over the completed S1 namespace.
Its immutable observations are:

| Observation | Value |
|---|---:|
| Published episodes | 49 |
| Namespace entities | 245 |
| Namespace EntityEdge facts | 183 |
| Returned EntityEdges | 10 |
| Distinct source sessions after edge attribution | 6 |
| Gold sessions | 2 |
| Matched gold episodic nodes | 2 |
| Gold episode entity mentions | 9, 1 |
| Gold episode EntityEdge counts | 0, 0 |
| Service errors | 0 |
| Historical edge-attributed source-session coverage | 0.0 |
| Downstream QA Accuracy | 0.0 |

The Reader and Judge each completed exactly once. Their successful transport
and parse statuses show that the zero result is upstream of Judge parsing. They
do not establish Reader quality because the Reader never received evidence
attributed to a gold session.

The zero EntityEdge counts are also compatible with pinned Graphiti extraction
rules: generic object descriptions need not become named entity endpoints, and
an EntityEdge requires valid source and target entities. This explains a
plausible representation-loss mechanism, but the sealed classification remains
limited to observed provenance rather than claiming a complete semantic cause.

## Upstream code comparison

### Graphiti 0.29.3

Pinned source behavior is unambiguous:

| API/config | Returned/ranked surface | Meaning |
|---|---|---|
| `Graphiti.search()` | `list[EntityEdge]` | Basic out-of-box fact/relationship search |
| `EDGE_HYBRID_SEARCH_RRF` | Edge BM25 + edge cosine, RRF | Recipe used by basic `search()` |
| `Graphiti.search_()` | `SearchResults` | Advanced configurable search |
| `COMBINED_HYBRID_SEARCH_RRF` | Edges, nodes, episodes, communities | Multi-surface RRF recipe |
| `COMBINED_HYBRID_SEARCH_CROSS_ENCODER` | Edges, nodes, episodes, communities | Multi-surface cross-encoder recipe |

The basic API is a legitimate Native Graphiti baseline. It is simply an edge
baseline, not a session retriever. The upstream docstring itself recommends
`search_()` when a more robust surface is required.

### LongMemEval pinned implementation

The pinned LongMemEval implementation uses one corpus item per session for the
`flat-session` path. Its top-k unit is a unique session, and its retrieval
metrics compare ranked session IDs against `answer_session_ids`.

The historical S2 adapter instead ranked 10 edges and deduplicated sessions
after ranking. Multiple edges from one session can consume multiple top-k
positions. Consequently:

```text
edge top-10 + provenance projection != flat-session top-10
```

The historical field named `evidence_recall_at_10` must be interpreted only as:

```text
edge-attributed source-session coverage at Edge@10
```

Official LongMemEval session Recall@10 was not computed.

### Published-system methodology

The following sources were checked by paper and code identity, without binding
this project to their exact numeric results:

| Source | Relevant design lesson |
|---|---|
| LongMemEval, ICLR 2025, arXiv 2410.10813 | Separates indexing, retrieval, and reading; evaluates evidence at session granularity; uses oracle retrieval to isolate Reader quality. |
| Zep temporal knowledge graph paper, arXiv 2501.13956 | Its reported retrieval context combines fact edges and entity nodes; it is not equivalent to basic edge-only `Graphiti.search()`. |
| vLLM, SOSP 2023, arXiv 2309.06180 | Performance comparisons hold model-quality behavior fixed rather than allowing serving changes to alter accuracy. |
| DistServe, OSDI 2024, arXiv 2401.09670 | Uses stage decomposition and validates modeled behavior against real execution before broad evaluation. |
| OSDI 2024 artifact-evaluation guidance | Uses a small kick-the-tires gate before full evaluation and requires claims to match the artifact's actual interface. |

These precedents support the current small-first stop. They do not justify
silently switching retrieval APIs and reusing the old run ID.

## Additional reproducibility gaps found

These gaps did not cause the observed zero edge coverage, but they block a
paper-grade S3 freeze:

1. The current S0 construction identity records model repository revision
   `aa55da1ecc13d006e8b8e4f54579b1ea8c3db2df`, while legacy runtime source still
   contains historical revision `6e2312b85c2ae9a31f629f24493b79d8b02eab1a`.
   The S2 compatibility check did not bind this field.
2. Construction-side OpenAI and Graphiti retry layers are not fully frozen in
   the U0 identity. Hidden retries affect latency, cost, and reproducibility even
   though no service failure occurred in this run.
3. The historical adapter identity hashed `graphiti.py` but not the underlying
   search implementation and recipe files.
4. The historical Reader identity declared `retriever_type=flat-session` even
   though its inputs were EntityEdge fact strings.

The future contract now binds the Graphiti search implementation, config,
recipes, and utility sources. Model revision and construction retry identity
must be repaired in a separate pre-reauthorization gate because changing those
fields retroactively would invalidate historical evidence.

## Code-design correction implemented

The correction is deliberately limited to future executions:

1. `s2_retrieval_contract.py` defines separate edge and session surfaces.
2. An edge top-k declares `retrieval_unit=EntityEdge` and `top_k_unit=edge`.
3. Edge provenance is reported as
   `edge_attributed_source_session_coverage_at_10`.
4. `official_longmemeval_session_recall_at_10` is explicitly `null` for the
   edge surface.
5. Reader identity declares `input_representation=EntityEdge.fact` and no
   longer claims `flat-session` item semantics.
6. Adapter identity v2 contains an independently validated retrieval contract.
7. Future execution provenance binds `search.py`, `search_config.py`,
   `search_config_recipes.py`, and `search_utils.py` in addition to
   `graphiti.py`.
8. Future diagnosis output prohibits inference about node, episode, or
   whole-graph quality from zero EntityEdge provenance.
9. A pure, dependency-injected episode-BM25 probe policy is implemented and
   tested, but has no command-line/live entry point and carries no live
   authorization.
10. The historical offline scaffold refuses to overwrite an already finalized
    S2 sanity artifact and can no longer emit the ambiguous retrieval field.
11. The diagnosis finalizer now refuses to overwrite an existing diagnosis or
    stage ledger, protecting the immutable historical chain by construction.
12. The future probe rejects any config with edge/node/community scopes,
    non-BM25 episode search, non-RRF reranking, a nonzero reranker threshold, or
    a limit different from top-k before issuing a retrieval call.

The historical artifacts retain their original bytes and hashes. The new
review artifact provides the interpretation overlay rather than rewriting the
past run.

## Minimal next experiment, not yet authorized

The correct next action is not another construction run and not an automatic
retry of the same S2 chain. It is one bounded, read-only `S2-R0` retrieval
surface diagnostic over the immutable S1 namespace.

The diagnostic should have these fixed properties:

```text
construction calls       = 0
database writes          = 0
namespace cleanup        = 0
Reader calls             = 0
Judge calls              = 0
raw content persistence  = 0
new run ID               = required
```

It should compare only:

1. already observed basic edge reachability;
2. official Graphiti advanced node reachability;
3. official Graphiti episode reachability;
4. direct gold representation coverage by scope.

Only counts, ranks, scope labels, public configuration, and hashes may be
persisted. Any embedding request required by a native search recipe must be
counted explicitly. No construction LLM request is allowed.

The prespecified decision table is:

| Observation | Decision |
|---|---|
| Gold episodic mapping is incomplete | Data/pipeline defect; requalify S1 before any quality run. |
| Episode/node scope reaches gold but edge scope cannot | Edge-surface limitation; define a new versioned retrieval policy before rerunning S2. |
| All native scopes miss despite valid mapping | Construction/index/search weakness; do a bounded representation audit, not a Reader retry. |
| A declared surface reaches gold but controlled QA fails | Only then isolate Reader and Judge with an oracle-style check. |

This diagnostic must not silently choose the best-performing surface. Its role
is to identify the interface that the frozen plan failed to specify. Selecting
a paper baseline afterward is a separate, versioned decision applied equally
to U0, A0, P*, and M*.

For a session-ranked diagnostic, the persisted retrieval metrics must remain
separate:

```text
session_recall_any_at_10
session_recall_all_at_10
session_gold_coverage_fraction_at_10
```

The first two match LongMemEval's per-item binary any/all semantics; the third
is an explicitly named diagnostic fraction and must not replace them.

## Current state and stop condition

```text
S0 = PASS
S1 = PASS
S2 historical numeric chain = terminal near-zero edge-surface diagnostic
S2 retrieval contract review = completed offline
S3 = NOT AUTHORIZED
additional live actions during review = 0
```

The project should not start S3, D0, or a replacement S2 live run until the
read-only diagnostic is explicitly authorized and its offline TDD/provenance
gate is green.

## Durable evidence

Primary review artifact:

```text
artifacts/paper_eval/native/S2_RETRIEVAL_CONTRACT_REVIEW.json
```

Historical evidence remains at:

```text
artifacts/paper_eval/native/U0_REFERENCE_SANITY.json
artifacts/paper_eval/native/S2_NEAR_ZERO_ROOT_CAUSE.json
artifacts/paper_eval/native/runs/s2-live-20260814-001/
```

TDD sequence for this correction:

```text
baseline full offline       92 passed
retrieval-contract RED      expected failures observed
contract focused GREEN      7 passed
integration RED             6 failed, 25 passed
integration focused GREEN   45 passed
full offline GREEN          111 passed
review artifact RED         missing artifact failure observed
review artifact GREEN       1 passed
future-source hash RED       stale hash failure observed
```
