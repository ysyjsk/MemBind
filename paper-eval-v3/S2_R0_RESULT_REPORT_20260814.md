# S2-R0 Episode-Surface Diagnostic Result

Status: completed bounded diagnostic. This report does not select the paper
retrieval policy or authorize S3.

## 1. Outcome

Replacement attempt `s2r0-20260814-002` completed successfully after the
attempt-001 harness defect was reproduced and repaired under TDD.

```text
history                               07741c45
namespace                             pev3-s1-20260814-001
observed/expected sessions            49 / 49
retrieved sessions                    10
gold sessions                         2
gold ranks                            1 and 2
covered gold sessions                 2
Recall_any@10                         1.0
Recall_all@10                         1.0
gold coverage fraction@10             1.0
```

The amendment's metric branch is:

```text
EPISODE_SURFACE_RECALL_ALL
```

The artifact's comparative classification is:

```text
EDGE_SURFACE_COVERAGE_GAP_CONFIRMED
```

These represent two views of the same observation: every gold session is
reachable on the tested Episode BM25/RRF surface, while the historical basic
EntityEdge surface had zero attributed gold-session coverage.

## 2. Exact execution contract

```text
retrieval API                         Graphiti.search_
Graphiti                              0.29.3 pinned source
retrieval unit                        EpisodicNode / one frozen session
search method                         Episode BM25
reranker                              reciprocal rank fusion
candidate limit                       20
returned top k                        10 sessions
search filters                        empty
query vector                          absent
temporal filter                       none
question date used                    false
```

Canonical retrieval-config SHA256:

```text
411df587095daf9284ffaa8399a66886e88329999d934a26e28e0d43caad7d46
```

Dataset and corpus identities:

```text
dataset
  d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442

frozen split
  747946a8792422ea35e9d56b864efb1a137cb6eb8a8e16f97808fe86f938c091

frozen 49-session corpus
  fb54ffa48d426bab3b91f22e528dfc98f0f223d00432f49ace2d996a1b19c0fe

expected/observed name-content map
  48a2ac78b160a4a6d06d1fd0c0c88f24dc6315540f350e25caa97d68dd411948
```

## 3. Read-only and no-model evidence

```text
Graphiti.search_ calls                 1
Neo4j read requests                    2
construction LLM requests              0
embedding requests                     0
cross-encoder requests                 0
Reader requests                        0
Judge requests                         0
database mutation attempts             0
database mutations                     0
namespace cleanup calls                0
retry count                            0
driver auto-schema initialization      false
```

The two Neo4j reads are the exact corpus guard and the Episode full-text search.
No vLLM or embedding service was needed for this diagnostic.

## 4. Evidence chain

```text
retry-002 offline qualification
  e27aa36c9ba7d99c7d74de8c64e43babc323c5a0528abfa4e0b14e4604a2454d

retry-002 one-shot authorization
  7e2e2ede9669fd2541b0e512a59ba2dea375d5441696c01889925a51d49c1d46

retry-002 exclusive consumption
  73b0a4917915bfb8975980e309b722b7ef52e3f8687220a1f5adcabd7a51b8d0

retry-002 result
  acc944dc9f60301dbd3d8ad45b7c8ceeb80320446ef3a315696d46a83294e741
```

The result file binds the authorization and consumption hashes, all 48 source
and evidence bindings, the repaired probe source, both new final JUnit files,
and the immutable attempt-001 failure chain.

Attempt 001 remains unchanged:

```text
authorization
  0a83291a4455013a5476e17ba3e9443eb9761ca55acd05b8fbd6a502f2be023a

consumption
  564e2ee43d7810280d40edefa3a9050e9b1025af974161e94482a07c182acb7d

failure
  f5709742e6f2209ebfa72d6b8d7b7566af7649774b34adc76819740cd40f71ff
```

## 5. TDD evidence

The attempt-001 `query` parameter collision was first reproduced with the
pinned production call shape, then repaired by renaming only the read-only
guard's positional Cypher parameter to `cypher_query_`.

```text
repair targeted                           1 / 1 passed
repair probe module                      12 / 12 passed
repair S2-R0 focused                     51 / 51 passed
repair full offline                     146 / 146 passed
retry-002 wiring                          4 / 4 passed
retry-002 final focused                  55 / 55 passed
retry-002 final full offline            150 / 150 passed
```

## 6. Research interpretation

This result supports the following bounded statement:

> For the frozen development history, the exact Native Graphiti Episode
> BM25/RRF surface retrieves both LongMemEval gold sessions in the first two
> ranks, despite zero gold-session attribution on the previously tested basic
> EntityEdge surface.

It provides strong evidence that the historical near-zero result was a
retrieval-surface mismatch, not proof that the relevant sessions were absent
from the constructed memory.

It does not establish:

```text
whole-Graphiti retrieval quality
EntityNode or CommunityNode quality
multi-surface quality
QA Accuracy
Reader/Judge correctness
generalization beyond this one exposed history
the final paper retrieval policy
S2 PASS under the parent protocol
S3 authorization
```

The result artifact correctly preserves:

```text
retrieval_policy_selected = false
s3_authorized = false
whole_graph_quality_conclusion = NOT_INFERRED
```

## 7. Naming note

The amendment names the `Recall_all@10=1` branch
`EPISODE_SURFACE_RECALL_ALL`, while the preregistered comparison function and
artifact use `EDGE_SURFACE_COVERAGE_GAP_CONFIRMED` when historical edge coverage
is zero. The numeric fields are unambiguous and internally consistent, but a
future post-R0 protocol should define a distinct `metric_outcome` and
`cross_surface_interpretation` rather than overloading one classification
field. The sealed result must not be rewritten to change this label.

## 8. Required next stage

Before Native U0 can be frozen, a separately approved S2-completion plan must
select a retrieval/Reader/Judge contract from architecture and benchmark
semantics, not from this development score, then produce formal Evidence
Recall@10 and QA Accuracy sanity evidence.

The prepared non-authoritative interface/TDD draft is
`S2_POST_R0_OFFLINE_DESIGN_DRAFT_20260814.md`. No S3 artifact or authority has
been created in this execution.
