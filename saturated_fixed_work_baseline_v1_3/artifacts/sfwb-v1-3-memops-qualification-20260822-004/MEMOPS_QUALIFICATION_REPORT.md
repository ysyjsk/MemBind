# MemOps Minimal Baseline Qualification

## Final gate

`STOP_MEMOPS_GRAPHITI_B0_INELIGIBLE`

B1 was not run. The B0 eligibility contract requires every frozen sample to
pass both the official MemOps QA and graph-level current-state inspection.
A28 failed the latter, so running B1 would violate the frozen qualification
order and could not establish a paired B0-versus-B1 state divergence.

## Frozen workload

| Sample | Target | Confirmed old -> new | Official QA |
|---|---|---|---|
| A01 | `job_title` | Junior Data Analyst -> Senior Data Analyst | 2/2 |
| A05 | `birchwood_unit_number` | Unit 3B -> Unit 4A | 2/2 |
| A13 | `metformin_dosage` | 500mg twice daily -> 1000mg twice daily | 2/2 |
| A14 | `brazilian_passport_number` | HG284913 -> HG284139 | 2/2 |
| A28 | `voss_sabbatical_start_date` | June 1st -> July 1st | 2/2 |

The selection and official judge implementation were frozen before live B0.
Each sorted MemOps segment was mapped to one existing `EpisodeInput`, with the
gold-blind reference-time mapping `2000-01-01T00:00:00Z + source_sequence
minutes`. Each sample used a fresh namespace.

## B0 result

- Five of five samples completed construction, serial durable publication,
  seal, read-only QA, and current-state inspection.
- Official MemOps QA: 10/10 correct, with zero stale-value errors.
- QA isolation: zero construction calls, zero graph write attempts, and no
  canonical graph hash mutation.
- Graph-level current-state inspection: 4/5 samples passed.
- A28 failed because the expected current value `July 1st` was absent from
  every canonical entity and entity edge.

## A28 causal audit

The frozen second segment explicitly states that Dr. Voss's sabbatical moved
from June 15th to July 1st. The following segment repeats July 1st as the last
confirmed value. B0 processed both segments serially and all phases returned
success.

The first provable loss is the native Graphiti extraction boundary:

1. Source sequence 1's `extract_edges.edge` call returned only 142 output
   tokens.
2. Its final canonical contribution is exactly one unrelated edge:
   `Sara's advisor is Dr. Kim.`
3. Source sequence 2 also contains July 1st, but none of its four canonical
   edges represent that value.
4. Resolution, transaction, and publication completed over this incomplete
   extraction output; they cannot recover a fact that was never materialized.
5. The final entity summaries retain June 15th, while `July 1st` has zero
   canonical entity/edge mentions.

This is not a false failure caused by choosing the 2000 observation time:
changing observation time cannot make an absent `July 1st` edge active. The
reference-time policy does affect how explicit calendar dates are interpreted,
but it is not causal for the missing latest value in this run.

The Reader still answered both A28 questions correctly because read-only
retrieval can expose episodic evidence containing July 1st. That is useful QA
behavior, but it does not satisfy the stricter requirement that Graphiti's
constructed current state itself encode the confirmed update.

Raw construction LLM responses were not persisted, so the audit does not claim
which exact JSON fields the model emitted. The supported root-cause boundary is
`NATIVE_GRAPHITI_EXTRACTION_RECALL`, not resolution, invalidation, persistence,
QA, concurrency, or publication.

## Decision

MemOps is not qualified as a B1 attack benchmark under the current frozen
five-sample adapter and native Graphiti B0. The experiment stops before B1.
No V5, scheduler, baseline semantics, or sealed artifact was modified.
