# P(C=4) Analysis

Scope: `BASELINE_REUSE_4_HISTORY_AUTHORED_EXTENSION`. P(C=4) is analyzed only where an already-persisted, source-bound namespace exists. This is not a construction run and not an official MemoryAgentBench Multi-QA result.

Coverage: 1/4 histories have a verified P(C=4) namespace.
Available histories: 07741c45.
Missing histories: b6019101, 6071bd76, a2f3aa27.

Status: **PARTIAL_BLOCKED_FOR_FULL_METHOD_CLAIM**

A four-history P(C=4) QA accuracy is not reported because three planned namespaces are absent from Neo4j. The partial score below is descriptive only for the available history and must not be compared as a full method result.

## Available-History Partial Score

- History: `07741c45`
- Primary accuracy: 3/4 = 75.0%
- Valid-only accuracy: 75.0%
- Reader invalid: 0; Judge invalid: 0

- Retrieval means: R@1 0.250, R@3 0.750, R@5 0.750, R@10 1.000, MRR 0.528, nDCG@10 0.641.

## Same-History Comparison

| Question | U0 | P(C=2) | P(C=4) | Gold rank P(C=4) |
|---|---:|---:|---:|---:|
| `07741c45-ext-001` | correct | correct | correct | 2 |
| `07741c45-ext-002` | wrong | correct | wrong | 9 |
| `07741c45-ext-003` | correct | correct | correct | 2 |
| `07741c45-ext-004` | correct | correct | correct | 1 |

For the only available history, U0 is 3/4, P(C=2) is 4/4, and P(C=4) is 3/4. This is a within-history diagnostic, not a four-history comparison.

## Identity And Safety

- P(C=4) namespace identity comes from the existing C246 plan and is checked against the live episode corpus.
- Missing namespaces are not substituted with U0, P(C=2), native, or another C246 block.
- No memory construction, Neo4j write, or namespace mutation was performed.
- Reader/Judge model: `Qwen/Qwen3-32B`; embedding model: `Qwen/Qwen3-Embedding-0.6B` (1024 dimensions).
- A partial score cannot support equivalence, non-inferiority, or a four-history P(C=4) conclusion.
- On the available history, P(C=4)'s only miss is the sandal-brand question: gold session rank 9, exact authored quote absent from the final context, and the Reader selected Teva + Keen rather than Teva + Merrell.
