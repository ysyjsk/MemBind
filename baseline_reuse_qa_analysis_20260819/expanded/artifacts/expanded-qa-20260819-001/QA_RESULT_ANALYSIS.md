# Final QA Result Analysis

Scope: `BASELINE_REUSE_4_HISTORY_AUTHORED_EXTENSION`. This is a post-hoc analysis of the 16 authored questions run against the same four frozen U0 and P(C=2) baseline states. It is not official MemoryAgentBench Multi-QA and is not a 240-question result.

## Outcome

- U0: 13/16 primary accuracy (81.2%); invalid outputs 0.
- P(C=2): 12/16 primary accuracy (75.0%); invalid outputs 1; valid-only diagnostic rate 80.0%.
- Primary delta P(C=2) minus U0: -6.2% (-6.2 percentage points).
- Paired agreement: 13/15 jointly valid pairs; 1 pair contains an invalid output; valid discordances: 07741c45-ext-002, 6071bd76-ext-004.

Invalid outputs are counted as incorrect in primary accuracy. The valid-only rate is retained only to separate semantic correctness from operational invalidity.

## Per-History

| History | U0 | P(C=2) |
|---|---:|---:|
| `07741c45` | 3/4 (invalid 0) | 4/4 (invalid 0) |
| `6071bd76` | 4/4 (invalid 0) | 3/4 (invalid 0) |
| `a2f3aa27` | 3/4 (invalid 0) | 2/4 (invalid 1) |
| `b6019101` | 3/4 (invalid 0) | 3/4 (invalid 0) |

## Retrieval Versus Reading

All 32 method/question rows retrieved the gold session within top-10 (R@10 = 1.000 for both methods), and post-hoc gold-session context coverage was 1.000. This rules out a simple missing-namespace explanation for the scored failures.

The exact gold quote was present in the final Reader context for 13/16 questions for each method. The three quote-absent questions were the sandal-brand question, the Sunday-meal question, and the shoe-collection organization question. The failures were concentrated in evidence selection and temporal/conflict resolution after session retrieval, not in complete session recall.

## Failure Attribution

- `07741c45-ext-002` (U0 only): the gold session ranked 9 and its exact quote was absent from the context. The context contained a long recommendation list with Teva, Keen, and Merrell; U0 answered Teva + Keen. P(C=2) had additional graph facts for Merrell and Teva and answered correctly.
- `6071bd76-ext-004` (P(C=2) only): the gold session ranked 1 and the Earl Grey quote was present. P(C=2) selected a conflicting later rose-tea discussion from the same history; U0 selected Earl Grey. This is a temporal/semantic conflict-resolution failure, not a session-recall failure.
- `a2f3aa27-ext-002` (both methods): the gold session ranked 1, but the exact Sunday sentence was absent from the final context. Slow-cooker-chili facts were present, yet neither Reader committed to the Sunday plan. This is a context-pack completeness and answer-time alignment failure.
- `a2f3aa27-ext-003` (P(C=2) only): the spreadsheet quote was present and the gold session ranked 1, but the Reader transport returned an invalid service response. This is an operational invalid, not a semantic miss; under the conservative primary metric it counts as incorrect.
- `b6019101-ext-004` (both methods): the gold quote was present and ranked 1, but the context also contained several graph facts pairing The Goonies with The Lion King and/or Back to the Future. Both Readers returned The Goonies + The Lion King instead of the authored target pair. This is a conflict-resolution failure caused by redundant, mutually inconsistent abstractions in the context.

## Interpretation

The frozen states are capable of retrieving the relevant sessions: mean retrieval was R@1 0.750, R@3 0.938, R@5 0.938, R@10 1.000, MRR 0.851, and nDCG@10 0.887 for both methods. The observed QA gap is therefore primarily downstream of retrieval: context packing, source-round truncation, temporal ordering, and resolution of conflicting graph facts.

P(C=2) is 75.0% on the conservative 16-question denominator versus U0 at 81.2%. Its valid-only rate is 80.0%. The two valid semantic discordances offset in aggregate: P(C=2) wins the sandal-brand pair while U0 wins the Earl Grey pair. The net 6.2-point primary gap is therefore driven by P(C=2)'s single Reader invalid, which is counted as incorrect conservatively. This is a diagnostic signal only; it cannot establish equivalence, non-inferiority, or MemoryAgentBench Multi-QA generalization.

The run used exact Reader/Judge model `Qwen/Qwen3-32B` and embedding model `Qwen/Qwen3-Embedding-0.6B` with 1024 dimensions. No construction occurred, all eight namespace snapshots were unchanged, and the protected source root was unchanged.
