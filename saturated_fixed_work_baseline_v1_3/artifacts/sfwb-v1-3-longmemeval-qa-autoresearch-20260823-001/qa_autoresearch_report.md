# LongMemEval QA Autoresearch Decision

Status: `QA_PROTOCOL_AUTORESEARCH_COMPLETE`

Decision: `GO_FREEZE_LAYERED_REVIEWER_SAFE_QA_PROTOCOL`

B1 attack gate: `STOP_NO_REPRODUCIBLE_B0_PASS_B1_FAIL`

## Why This Is Reviewer-Safe

The protocol does not search for a favorable score. It separates the four quantities that are conflated by a single Reader number: official end-to-end answer quality, official session evidence recall, Reader upper-bound calibration, and graph-state representation.

- Headline: official LongMemEval session-value Reader + official Judge, with model-visible text read only from persisted Neo4j `EpisodicNode.content`.
- Retrieval: gold session IDs are used only after Reader completion for recall metrics.
- Calibration: gold-only sessions are an upper bound and are not headline performance.
- State: facts/entities and strict current-state predicates remain diagnostics.

## Candidate Results

| Lane | B0 | B1 | Authority | Interpretation |
| --- | ---: | ---: | --- | --- |
| A session-value | 2/4 | 2/4 | headline eligible | recall@10 = 1.00; two graphs lack later state evidence |
| B facts + entities | 0/4 | 0/4 | diagnostic only | Zep-shaped graph surface is incomplete for this cohort |
| C strict current-state | 0/4 | 0/4 | diagnostic only | B0 eligibility is not met |
| D oracle sessions | see artifact | see artifact | calibration only | isolates Reader capability from retrieval noise |

## Paired Claim

Across all four histories and both live lanes, there is no `B0 PASS -> B1 FAIL` outcome. Therefore the data do not authorize a claim that Naive Whole-Update Async is semantically unsafe on these completed graphs. This is a protocol result, not a request to relax the predicate.

The missing evidence boundary is observable: Candidate A retrieves both official answer sessions at rank <= 10 for every history, yet `07741c45` and `a2f3aa27` do not contain the later current-state fact in the persisted graph. Candidate A therefore reports a valid 0.5 end-to-end score while the retrieval recall remains 1.0, making the failure attributable to graph evidence coverage rather than retrieval selection.

## Frozen Reporting Rule

Use lane A as the only headline B0/B1 quality number. Publish its evidence recall beside it. Include lane D as Reader calibration, and lanes B/C as graph usability/state diagnostics. Do not combine these denominators and do not infer a B1 failure from canonical UUID/order differences.

No construction call, Neo4j write, scheduler change, V5 start, or existing artifact mutation occurred in this autoresearch round.
