# Expanded QA analysis over frozen baseline states

Scope: `BASELINE_REUSE_4_HISTORY_AUTHORED_EXTENSION`. This is an authored QA extension over the same four frozen baseline histories, not the official MemoryAgentBench Multi-QA dataset and not a 240-QA result.

No construction was performed; U0 and P(C=2) reused their existing sealed namespaces and the same 16-question inventory.

| Method | Valid | Correct | Accuracy | Wilson 95% interval |
|---|---:|---:|---:|---:|
| U0 | 16/16 | 13 | 81.2% | [57.0%, 93.4%] |
| P(C=2) | 15/16 | 12 | 75.0% | [50.5%, 89.8%] |

Reader valid: U0 16/16; P(C=2) 15/16.
Invalid outputs: U0 reader 0, judge 0; P(C=2) reader 1, judge 0. Invalid outputs count as incorrect in primary accuracy; valid-only P(C=2) accuracy is 80.0%.
Paired agreement: 13/15 jointly valid pairs (86.7%); 1 pair contains an invalid output; 2 valid pairs are discordant.
Observed P(C=2)-minus-U0 primary accuracy delta: -6.2%. This small diagnostic cannot establish equivalence or non-inferiority.

Exact live models: Reader/Judge `Qwen/Qwen3-32B`; embedding `Qwen/Qwen3-Embedding-0.6B` (1024 dimensions).
Mean retrieval metrics (identical here for both methods): R@1 0.750, R@3 0.938, R@5 0.938, R@10 1.000, MRR 0.851, nDCG@10 0.887; post-hoc gold-session context coverage was 1.000 for both.

All retrieval metrics are session-level metrics; provenance coverage is a post-hoc diagnostic. Gold labels were withheld from retrieval and Reader projections and used only for post-retrieval metrics/Judge evaluation.
