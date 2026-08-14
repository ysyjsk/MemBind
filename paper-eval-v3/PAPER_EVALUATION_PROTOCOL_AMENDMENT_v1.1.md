# Paper Evaluation v3 Retrieval and Quality Amendment v1.1

Status: execution-scoped amendment; it does not overwrite the parent protocol
or any completed S0-S2 artifact.

Parent protocol:

```text
../(main experiment) MemBind_PAPER_EVALUATION_WORKPLAN_v3_SMALL_FIRST_FINAL.md
SHA256 = 4b81c89b33d407f04fc20862a81eab6badba16d0d61d98331cbe188d1bb4f41e
```

The actual parent filename uses the repository's Chinese full-width prefix;
the hash above is the authoritative identity.

## 1. Research claim boundary

MemBind is a construction-runtime mechanism. It does not claim to improve the
retrieval algorithm. A paper-level performance claim is admissible only when
benchmark utility and construction-sensitive graph semantics remain within a
predeclared quality gate.

The evaluation therefore keeps three surfaces distinct:

1. `graphiti_basic_edge`: the upstream `Graphiti.search()` EntityEdge surface.
   Its top-k unit is an edge and its diagnostic metric is
   `edge_attributed_source_session_coverage_at_10`. It is not LongMemEval
   Session Recall@10.
2. `graphiti_episode_bm25_session_diagnostic`: one EpisodicNode per frozen
   LongMemEval session, ranked by upstream `Graphiti.search_()` with episode-only
   BM25 and RRF. It computes binary LongMemEval-style `Recall_any@10` and
   `Recall_all@10`. This diagnostic probe does not select the final paper retrieval policy.
3. Graph-sensitive correctness: canonical/semantic graph parity, provenance,
   temporal and publication invariants, lost/duplicate updates, and direct violations.
   Episode QA cannot substitute for this surface.

The historical `s2-live-20260814-001` attempt is immutable, terminal, and
non-mergeable. Its zero retrieval value is interpreted only on the basic edge
surface. S3 remains unauthorized.

## 2. Literature-backed decisions

The following decisions are adopted:

- LongMemEval separates indexing, retrieval, and reading, and defines evidence
  with answer session IDs. Retrieval coverage and QA must therefore be reported
  separately. Source: LongMemEval, arXiv:2410.10813 and official code
  `xiaowu0162/LongMemEval`.
- Zep/Graphiti evaluates a broader graph context than the basic edge-only API.
  The basic API remains a valid Native compatibility baseline, but cannot stand
  in for the full Graphiti-family quality pipeline. Source: Zep, arXiv:2501.13956
  and Graphiti v0.29.3 source.
- Systems comparisons must preserve output semantics or quality constraints.
  vLLM compares serving performance without changing model accuracy, while
  DistServe defines goodput under latency constraints. These support a frozen
  quality gate before headline performance claims. Sources: arXiv:2309.06180
  and arXiv:2401.09670.
- Small-first qualification and claim-to-artifact traceability follow the OSDI
  artifact-evaluation methodology: diagnose a bounded pipeline before scaling
  the formal evaluation.

The following recent-memory evidence is retained only with narrower wording:

- Mnemis (arXiv:2602.15313, ACL 2026) uses a modified Graphiti-based pipeline.
  Its QA ablations show complementarity between raw Episodes and the combined
  Entity+Edge graph representation. It does not prove that each Native Graphiti
  surface is independently complete, and it does not provide a graph-parity
  correctness gate.
- LiCoMemory (arXiv:2511.01448) reports accuracy, a target-coverage recall,
  latency, and retrieved tokens. Its recall is not LongMemEval binary
  `Recall_all@10`.
- TiMem (arXiv:2601.02845) reports QA quality, recalled-memory length and
  latency, and consolidation calls. It does not report Session Recall@k.

These papers support separate quality and efficiency reporting. They do not
define this amendment's metric semantics or justify choosing a retrieval policy
after observing development scores.

## 3. S2-R0 fail-closed corpus guard

Before the single search call, the live namespace must be read once and prove:

```text
expected episode count = observed episode count = 49
expected episode names = observed episode names
expected name -> content SHA256 map = observed name -> content SHA256 map
expected frozen session ID sequence = observed mapped session ID sequence
answer session IDs are a subset of the observed mapped session IDs
all names, UUIDs, and session IDs are unique
all observed group IDs equal pev3-s1-20260814-001
dataset SHA256 and frozen corpus SHA256 match the authorization
```

Any mismatch stops before `Graphiti.search_()`. A corpus or mapping defect is a
qualification failure, not a retrieval miss.

## 4. Exact S2-R0 retrieval contract

The only next live action is one read-only probe on history `07741c45` and
namespace `pev3-s1-20260814-001`:

```python
SearchConfig(
    edge_config=None,
    node_config=None,
    episode_config=EpisodeSearchConfig(
        search_methods=[EpisodeSearchMethod.bm25],
        reranker=EpisodeReranker.rrf,
    ),
    community_config=None,
    limit=10,
    reranker_min_score=0,
)
```

Additional fixed arguments are an empty SearchFilters object, no center node,
no BFS origin, no query vector, and exactly one group ID. Graphiti 0.29.3 uses
`2 * limit` BM25 candidates internally and RRF over the single candidate list.

The runtime is constructed with the Neo4j driver outside an active event loop,
so Graphiti's automatic index/constraint initialization task is absent. Every
database query must use read routing and pass a mutation-token denylist. Model,
embedder, and cross-encoder clients are fail-closed no-call guards. Reader and
Judge are not constructed.

Observed counters must include:

```text
construction_llm_requests = 0
embedding_requests = 0
cross_encoder_requests = 0
reader_requests = 0
judge_requests = 0
database_mutation_attempts = 0
database_mutations = 0
neo4j_read_requests > 0
graphiti_search_calls = 1
namespace_cleanup_calls = 0
retry_count = 0
```

## 5. Result interpretation

- `Recall_all@10 = 1`: `EPISODE_SURFACE_RECALL_ALL`; seal and stop. This shows
  that all gold sessions are reachable on the tested Episode surface. It does
  not establish whole-Graphiti retrieval quality.
- `Recall_any@10 = 1` and `Recall_all@10 = 0`:
  `PARTIAL_EPISODE_SURFACE_REACHABILITY`; seal and stop for offline diagnosis.
- `Recall_any@10 = 0`: `EDGE_AND_EPISODE_SURFACES_NEAR_ZERO`; seal and stop.
  EntityNode, CommunityNode, and multi-surface retrieval remain `UNTESTED`.
- Corpus/config/source/authorization failure: no retrieval conclusion; stop at
  the responsible qualification gate.

Every branch keeps `retrieval_policy_selected=false`, `s3_authorized=false`,
and `whole_graph_quality_conclusion=NOT_INFERRED`.

## 6. One-shot evidence and execution order

The offline qualification and one-shot authorization bind the dataset, split,
S1 checkpoint/events/summary, historical S2 interpretation, amendment,
Graphiti search implementation, Neo4j search operations/driver, the canonical
SearchConfig, and all new source/test hashes.

The fixed order is:

```text
RED tests
-> minimum implementation
-> focused GREEN
-> full offline GREEN
-> seal S2_R0_OFFLINE_QUALIFICATION.json
-> seal S2_R0_AUTHORIZATION.json
-> exclusively consume the authorization once
-> corpus preflight
-> one Graphiti.search_ call
-> seal S2_R0_EPISODE_PROBE.json (or sanitized failure)
-> STOP
```

No cleanup, rebuild, Reader/Judge call, retrieval-policy search, S3 transition,
or formal-method run is authorized by this amendment.
