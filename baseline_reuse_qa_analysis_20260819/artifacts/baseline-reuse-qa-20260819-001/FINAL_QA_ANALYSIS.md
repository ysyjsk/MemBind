# Baseline-reuse final QA analysis

This is a read-only reanalysis of the existing four-history Quality-v1 baseline. It is not a MemoryAgentBench Multi-QA result.

## Headline

| Method | Valid | Correct | Accuracy | Wilson 95% interval |
|---|---:|---:|---:|---:|
| U0 | 4/4 | 2 | 50.0% | [15.0%, 85.0%] |
| P(C=2) | 4/4 | 2 | 50.0% | [15.0%, 85.0%] |

The exact `Qwen/Qwen3-32B` rejudge produced 0 invalid outputs across 8 requests and agreed with the frozen original Judge on 8/8 rows.

## Paired interpretation

U0 and P(C=2) agree on 4/4 histories; the observed P(C=2)-minus-U0 accuracy delta is +0.0%. There are no observed paired wins or losses, but n=4 is far too small to claim equivalence.

## Retrieval and execution validity

| Method | Reader valid | R@1 | R@3 | R@5 | R@10 | MRR | nDCG@10 |
|---|---:|---:|---:|---:|---:|---:|---:|
| U0 | 4/4 | 0.500 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| P(C=2) | 4/4 | 0.500 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |

## Error analysis

Both methods fail the same two histories:

- `6071bd76`: both answers say *more water* even while describing a change from 6 oz to 5 oz; the reference direction is *less water*. This is a Reader reasoning/wording error, not an invalid Judge response.
- `a2f3aa27`: both answers return 1,250 Instagram followers while the current reference is 1,300. This is a stale/current-state answer error.

The two successes are also shared: the old sneakers location and the count of five MCU films. Because retrieval and final labels are identical across methods on every row, this sample contains no evidence that P(C=2) changes downstream QA quality relative to U0.

## Scope limits

- Only four development histories are scored per method.
- The result is a baseline-reuse diagnostic, not a MAB Multi-QA result.
- All four questions are knowledge-update questions; no question-type generalization is supported.
- Identical observed accuracy does not establish method equivalence or non-inferiority.
