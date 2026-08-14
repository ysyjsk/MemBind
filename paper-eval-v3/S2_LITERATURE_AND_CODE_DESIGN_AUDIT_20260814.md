# S2 Literature and Code-Design Audit

Date: 2026-08-14

Scope: evaluate the methodological advice in the proposed final evaluation
protocol and bind it to official papers, code, and the pinned Graphiti 0.29.3
implementation. This is an interpretation artifact, not a new live result.

## Verdict

The proposed three-surface separation, corpus completeness gate, metric-unit
naming, fixed evaluator policy, quality-preserving performance gate, and
append-only evidence rules are methodologically sound. They address a real
contract error in the historical S2 attempt: ten EntityEdges were ranked and
only later projected to sessions, so the resulting value was not LongMemEval
Session Recall@10.

Two claims require narrower wording:

1. Mnemis supports complementarity between raw Episode retrieval and a joint
   Entity+Edge graph representation in its modified Graphiti pipeline. It does
   not provide an Entity-only versus Edge-only completeness proof and does not
   establish Native Graphiti graph parity.
2. LiCoMemory and TiMem support reporting quality and efficiency separately,
   but their metrics are not interchangeable with binary LongMemEval
   `Recall_all@10`.

## Official sources

### LongMemEval

- Paper: https://arxiv.org/abs/2410.10813
- Repository: https://github.com/xiaowu0162/LongMemEval
- Audited repository commit:
  `9e0b455f4ef0e2ab8f2e582289761153549043fc`

LongMemEval separates indexing, retrieval, and reading. Its official retrieval
evaluation consumes ranked session items and compares them with
`answer_session_ids`. `recall_any` is binary coverage of at least one answer
session; `recall_all` is binary coverage of every answer session. This directly
supports recording retrieval and downstream QA as separate evidence.

The S2-R0 implementation is benchmark-unit aligned, but its retriever is
Graphiti 0.29.3 episode full-text BM25/RRF, not the official LongMemEval
retriever implementation. The artifact therefore records both the metric
semantics and the exact Graphiti retrieval identity.

### Graphiti / Zep

- Paper: https://arxiv.org/abs/2501.13956
- Repository: https://github.com/getzep/graphiti
- Pinned project version: Graphiti 0.29.3, repository commit
  `021d3a57d511f21b10adaf7fa923bd5c1fce5e9d`

The basic `Graphiti.search()` API returns EntityEdges. The advanced
`Graphiti.search_()` API can return edge, node, episode, and community objects
under an explicit SearchConfig. The Zep paper's LongMemEval context combines
more than the basic edge-only surface. Consequently, the historical Edge@10
diagnostic is a valid Native API characterization but not a complete
Graphiti-family quality reproduction.

Pinned source inspection also establishes the S2-R0 execution envelope:

```text
Graphiti.search_()
-> search()
-> episode_search()
-> episode_fulltext_search(candidate limit = 2 * top-k)
-> RRF over one BM25 result list
-> first top-k EpisodicNodes
```

With no edge/node/community cosine or MMR scope, search does not request an
embedding. Episode RRF does not request a cross encoder. Neo4j full-text search
uses read routing. A separate driver-construction guard is still required,
because constructing Neo4jDriver inside an active event loop schedules
index/constraint creation.

### Mnemis

- Paper: https://arxiv.org/abs/2602.15313
- Repository: https://github.com/microsoft/Mnemis
- Audited repository commit:
  `4552fed19bc0cde7b990a6ceb0365cd75b1b3453`

Sections 2.1-2.3 describe Episodes, Entities, Edges, and hierarchical graph
retrieval. Section 3.3.1 compares System-1 RAG (Episodes), Graph
(Entities+Edges), and their combination; the combined representation recovers
information lost through graph compression. This is evidence that structured
and unstructured representations can be complementary.

The public repository primarily releases Global Selection components rather
than a complete ingestion/System-1 reproduction. The paper does not isolate
Entity-only versus Edge-only retrieval and does not define graph-equivalence
invariants. Mnemis is therefore contextual evidence, not authority for the
MemBind correctness contract.

### LiCoMemory

- Paper: https://arxiv.org/abs/2511.01448
- Repository: https://github.com/EverM0re/LiCoMemory
- Audited repository commit:
  `a844d993f77f947f682a0a52ec2825f2950bc0b3`

LiCoMemory reports QA accuracy, retrieval recall, query latency, retrieved
tokens, and update-stage costs. Its official evaluator computes fractional evidence-session coverage: matched origin session IDs divided by all origin
IDs, averaged across items. That is not binary LongMemEval `Recall_all@10`.
It is a valid precedent for separate quality/retrieval/efficiency reporting,
not for renaming one metric as another.

### TiMem

- Paper: https://arxiv.org/abs/2601.02845
- Repository: https://github.com/TiMEM-AI/TiMEM
- Audited repository commit:
  `6d279a5f5d40ee229e1995df15c182cb2062c71c`

TiMem reports LLM-judge QA accuracy, recalled-memory tokens, recall P50/P95
latency, and consolidation LLM calls. It does not report LongMemEval Session Recall@k.
Its repository README also contains a mismatch between one expected
LongMemEval accuracy value and the paper, so paper claims and code identities
must be bound separately rather than treated as an artifact-grade protocol for
this project.

### Systems methodology

- vLLM (SOSP 2023): https://arxiv.org/abs/2309.06180
- DistServe (OSDI 2024): https://arxiv.org/abs/2401.09670
- OSDI 2024 artifact guidance:
  https://www.usenix.org/conference/osdi24/call-for-artifacts

vLLM preserves model semantics while comparing throughput/latency. DistServe
defines goodput under latency constraints and separates serving stages. The
artifact guidance supports small-first functionality checks and mapping paper
claims to reproducible evidence. Together they support MemBind's rule:
performance is a success only under a predeclared quality/correctness gate.

The stricter rule that the final evaluation policy cannot be chosen after
observing development scores comes from pre-registration and fair-comparison
logic. It should not be attributed to Mnemis, LiCoMemory, or TiMem, which do
perform development parameter studies.

## Adopted execution consequence

The only authorized next diagnostic, after offline RED/GREEN and sealed
authorization, is one read-only Episode BM25/RRF probe. Corpus mismatch stops
before search. Every result states that EntityNode, CommunityNode, and
multi-surface retrieval remain untested. A successful Episode result does not
authorize S3, choose the final retrieval policy, or replace the independent
graph-correctness lane.
