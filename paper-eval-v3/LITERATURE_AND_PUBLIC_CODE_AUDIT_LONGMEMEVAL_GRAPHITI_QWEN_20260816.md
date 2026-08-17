# LongMemEval / Graphiti / Qwen Literature and Public-Code Audit

Date: 2026-08-16

Status: development-stage audit; no held-out result was inspected

## Decision

No peer-reviewed paper or public artifact found in this audit reports an exact
numeric baseline for the current stack:

```text
Qwen3-32B-FP8 construction and Reader
+ OSS Graphiti 0.29.3
+ Qwen3-Embedding-0.6B
+ cleaned LongMemEval
+ Qwen Judge
```

The current four-question Native U0 value therefore cannot be compared
numerically with Zep, Mnemis, UnifiedMem, or the LongMemEval paper. The valid
labels are:

```text
PROTOCOL_ALIGNED_NOT_NUMERICALLY_MATCHED
PROTOCOL_RUBRIC_COMPATIBLE_LOCAL_JUDGE_DIAGNOSTIC
```

The completed U0 observation remains immutable:

```text
development questions             4, all knowledge-update
Session Evidence Recall@10        4/4
gold sessions                     8/8 at rank 1 or 2
Qwen Reader + Qwen Judge QA       1/4
```

This result locates the observed failure after session retrieval. It is not
evidence that Native Graphiti lost the annotated source sessions, and four
same-type questions are not an absolute memory-quality estimate.

## Audited 2024-2026 Evidence Map

The following table distinguishes formal publication, architectural overlap,
and the actual role of Qwen. "Not comparable" means that an absolute score
cannot be transferred to the current stack; it does not mean that the work is
irrelevant as design evidence.

| Work | Status | LongMemEval surface | Graphiti/Zep | Qwen role | Exact numeric comparator? |
| --- | --- | --- | --- | --- | --- |
| LongMemEval | ICLR 2025 | S, M, and Oracle; 500 questions each | No | Qwen2.5-7B Reader appendix | No |
| Zep | arXiv 2025 | original LongMemEval-S, 500 questions | Zep Cloud / Graphiti architecture | None | No |
| PREMem | Findings of EMNLP 2025 | LongMemEval-S, 500 questions | No | Qwen2.5 extraction/reasoning/answer families | No |
| LightMem | ICLR 2026 | LongMemEval-S, 500 questions | No | Qwen3-30B-A3B backbone | No |
| Mnemis | ACL 2026 Long Paper | LongMemEval-S, 500 questions | Graphiti-derived, substantially modified | Qwen3 embedding and reranking | No; closest Graphiti-derived evidence |
| UnifiedMem | ACL 2026 Long Paper | cleaned LongMemEval-S/M | Custom controlled graph | Qwen3-8B extraction and answering | No; closest controlled Qwen graph evidence |
| Memory-R1 | ACL 2026 Long Paper | Oracle LongMemEval | No | Qwen2.5 memory/answer agents | No |
| MAGMA | ACL 2026 Long Paper | LongMemEval | Custom multi-graph | None in the reported LongMemEval stack | No |

No audited work combines Native OSS Graphiti, Qwen3-32B construction and
reading, Qwen3-Embedding-0.6B, the cleaned LongMemEval release, and a Qwen
Judge. In particular, "uses Qwen" must not be collapsed across construction,
embedding, reranking, answering, and judging; those roles create different
confounds.

## LongMemEval (ICLR 2025)

Sources:

- https://openreview.net/forum?id=pZiyCaVuti
- https://arxiv.org/abs/2410.10813
- https://github.com/xiaowu0162/LongMemEval/tree/9e0b455f4ef0e2ab8f2e582289761153549043fc

LongMemEval separates indexing, retrieval, and reading. It reports retrieval
metrics separately from question answering, restores retrieved values to
chronological order before reading, and uses question-type-specific grading.
The official Judge is `gpt-4o-2024-08-06` at temperature zero. The paper's
human meta-evaluation uses 30 samples per question type and reports mean
agreement `0.98` for GPT-4o-generated answers and `0.97` for
Llama-3.1-8B-generated answers. Every category is at least `0.90`. That
evidence does not transfer to a Qwen Judge merely because the rubric text is
the same.

The published Qwen2.5-7B Reader results illustrate that Reader capacity and
context representation materially affect QA:

```text
Oracle sessions, direct / CoN       0.282 / 0.504
LongMemEval-S long context          0.128 / 0.144
LongMemEval-S RAG K=V / K=V+fact    0.452 / 0.462
LongMemEval-M RAG K=V / K=V+fact    0.390 / 0.424
```

These are full 500-question results under the paper's own configurations, not
expected values for the current four-question development slice.

The official benchmark contains 500 evaluation instances in each of its S, M,
and Oracle files. LongMemEval-S is approximately 115K tokens per instance;
LongMemEval-M contains roughly 500 sessions per instance. Dataset release,
deduplication, Reader, retrieval granularity, and Judge identity must all match
before claiming exact reproduction.

### `useronly` is a key/value distinction

Section 5.1 and the official retrieval code keep user utterances when a
session or round is used as the retrieval *key*. Reader values are governed by
a separate flag:

```text
run_generation.sh default       useronly=false
official README recommendation  useronly=false
```

The generation implementation supports `useronly=true` as an explicit
optional projection, but its existence does not make it the paper's default
Reader recipe. The published strong recipe combines chronological ordering,
JSON presentation, and Chain-of-Note while retaining the two-sided session
value by default.

Globally projecting Reader values to user turns removes evidence required by
`single-session-assistant`. The existing `reader-v3-useronly` result (`2/4`)
therefore remains a diagnostic ablation and must not replace the common
U0/A0/P Reader.

## Zep / Graphiti

Sources:

- https://arxiv.org/abs/2501.13956
- https://github.com/getzep/zep-papers/tree/4b7f26cc76cca20743314ba9acb8c2cb6adc42f6
- https://github.com/getzep/zep/blob/be263ee23085410185835e0d8508b47fd35e9abb/benchmarks/longmemeval/zep_longmem_eval.py

Zep is the closest architectural reference, but the paper is an arXiv report,
not a confirmed OSDI/SOSP/NSDI publication. Its LongMemEval path uses a
materially different quality stack:

```text
construction              gpt-4o-mini-2024-07-18
retrieval context          top-20 fact edges + top-20 entity summaries
temporal presentation      fact valid_at - invalid_at/present
Reader                     GPT-4o-mini or GPT-4o
Judge                      GPT-4o with type-specific LongMemEval rubric
reported LongMemEval-S     63.8% / 71.2%
mean answer context        about 1.6K tokens
```

The public code sends `<FACTS>` and `<ENTITIES>` to the Reader. It does not
reload ten complete raw sessions after retrieving Episodic nodes. The public
notebook uses Zep Cloud rather than OSS Graphiti and contains concrete
reproduction hazards: the ingestion cell filters for
`single-session-assistant`, a later evaluation loop assumes 500 items, and the
public Reader and grader calls are both hard-coded to `gpt-4o-mini` even though
the paper describes two Reader variants and a GPT-4o Judge. Therefore the
artifact documents the facts-plus-validity-plus-entities architecture, but it
is not a clean exact-reproduction harness for the reported table.

The Zep study also uses the original approximately 115K-token LongMemEval-S,
not the later cleaned/deduplicated release used by the current project. Its
`63.8% / 71.2%` values must be described as published reference points under a
different dataset and quality stack, never as an expected Native Graphiti
range.

Graphiti's current repository `tests/evals/eval_e2e_graph_building.py` uses
oracle data and `gpt-4.1-mini` to compare graph-building outputs. It is not an
end-to-end LongMemEval QA benchmark.

## Mnemis (ACL 2026 Long Paper)

Sources:

- https://aclanthology.org/2026.acl-long.1096/
- https://github.com/microsoft/Mnemis

Mnemis is the closest peer-reviewed `Graphiti-derived + Qwen + LongMemEval`
system found. It is explicitly based on Graphiti but changes the algorithm by
adding reflection, speaker constraints, tags, hierarchical categories, global
selection, and dual-route retrieval. Its representative stack is:

```text
embedding                 Qwen3-Embedding-0.6B, reduced to 128 dimensions
reranking                 Qwen3-Reranker-8B
database                  Neo4j
retrieved context         top-10 episodes, top-20 entities/categories/edges
construction/Reader       GPT-4o-mini or GPT-4.1-mini
Judge                     GPT-4.1-mini with the dataset rubric
dataset                   LongMemEval-S, 500 questions
```

Qwen is used for embedding/reranking, not as the Reader or Judge. Mnemis is
therefore useful design evidence but not a Native Graphiti numeric reference.
Its reported `91.6` LongMemEval-S result additionally includes Mnemis-specific
reflection, forced-speaker extraction, hierarchical categories, global
selection, and reranking; attributing that number to Graphiti itself would be
incorrect.

## UnifiedMem (ACL 2026 Long Paper)

Sources:

- https://aclanthology.org/2026.acl-long.1232/
- https://github.com/AvatarMemory/UnifiedMem/tree/3df9428e6a788d2c2ab6b859c85b937a0128ba2f

UnifiedMem is the closest peer-reviewed `Qwen Reader + graph memory + cleaned
LongMemEval` evidence. It explicitly controls extraction, indexing, retrieval,
context values, and answering. Its graph is not Graphiti and its Judge remains
GPT-4o. Representative Qwen3-8B results are:

```text
flat:   R@5 0.9069, R@10 0.9570, NDCG@5 0.9227, NDCG@10 0.9323
        session-value QA 0.618, key-value QA 0.600
graph:  R@5 0.9427, R@10 0.9618, NDCG@5 0.9495, NDCG@10 0.9540
        session-value QA 0.708, key-value QA 0.576
```

In its GPT configuration, graph retrieval reaches `R@5=0.9690` versus
`0.9284` for flat retrieval, and session-value QA reaches `0.892` versus
`0.760`. However, graph key-value QA is `0.690`, below the flat value of
`0.752`. This is strong evidence for reporting retrieval and Reader/context
usability separately rather than treating Recall as end-to-end QA.

## Other Relevant Published Work

Memory-R1 (ACL 2026,
https://aclanthology.org/2026.acl-long.583/) demonstrates Qwen-family memory
and answer agents over the Oracle LongMemEval data, but does not evaluate the
current construction/retrieval stack. MAGMA (ACL 2026,
https://aclanthology.org/2026.acl-long.1709/) evaluates a custom multi-graph
memory on LongMemEval with a GPT-4o-mini backbone. PREMem (Findings of EMNLP
2025, https://aclanthology.org/2025.findings-emnlp.1204/) demonstrates that
pre-storage representation and Reader family materially change QA. None is an
exact Graphiti/Qwen reference.

LightMem (ICLR 2026, https://arxiv.org/abs/2510.18866 and
https://github.com/zjunlp/LightMem/tree/8fc9a9179f9170c4a40fc653fcb410375900f26e)
is useful for an open-Qwen comparison. Its released LongMemEval path separates
construction, retrieval, answering, and judging; the Qwen example uses a Qwen
answer backbone and a separate Judge. The ICLR paper's LongMemEval evaluation
uses GPT-4o-mini as Judge; the repository additionally reports GPT-4o-mini and
Qwen2.5-32B Judge sensitivity for its LoCoMo baseline tables. Those LoCoMo
tables must not be cited as LongMemEval Judge qualification. LightMem is not
Graphiti, and its `user_only` setting controls which messages feed its own
metadata/summary construction, not LongMemEval's raw-session Reader values.

## Implications for MemBind

1. Continue the running development U0/A0/P(C=2) suite with its already-shared
   two-sided JSON+CoN Reader and Qwen Judge identity. This preserves the
   relative fairness experiment.
2. Keep the original U0 `1/4` result and the user-only `2/4` ablation. Do not
   rerun until a favorable generation appears.
3. Rename the current surface precisely: it is Session Evidence Recall@10 plus
   a raw-session Reader diagnostic. It does not exercise EntityEdge temporal
   semantics strongly enough to be the only Graphiti semantic QA claim.
4. Before PILOT/FINAL, freeze a method-independent graph-native quality lane:
   sealed namespace -> fresh Graphiti edge/node retrieval -> facts, validity
   ranges, and entity summaries -> fixed Reader -> official LongMemEval Judge
   or a separately human-qualified replacement.
5. Preserve both metrics. Session Evidence Recall@10 diagnoses whether source
   sessions were found; graph-derived QA diagnoses whether the constructed
   semantic/temporal graph remains usable.
6. Run the graph quality lane read-only after construction. Reader/Judge time
   is excluded from construction makespan, and all methods use identical
   retrieval, prompt, model revision, and Judge hashes.
7. Freeze this revision before any PILOT/FINAL outcome is viewed. Development
   observations and protocol revisions remain disclosed.
8. Keep the paper's primary systems claim on comparable semantic work,
   freshness, goodput, backlog, and makespan. QA is a frozen usability
   guardrail and must not be used to tune one scheduler differently.
9. Use reviewer-safe comparison language: `protocol-aligned reference`,
   `architectural evidence`, or `diagnostic result`. Reserve `reproduction`
   and direct percentage deltas for configurations matching dataset release,
   construction, retrieval context, Reader, Judge, prompt, and evaluation
   script.
