# V6 Semantic Root Audit

Status: **SEALED_PHASE_0_IDENTITY_LIVE_TREATMENT_UNAUTHORIZED**

DVSR's semantic root is the sealed Frozen V6 substrate.  This is a V6-preserving
claim: preparation may overlap with later stateful work, but the authoritative
state transition and durable publication remain V6's ordered chain.  The audit
does not claim that V6 is timing-only equivalent to B0; the paired B0 request
identity needed for that stronger claim is not present in the checked-in V6
artifacts.

## Frozen identity

- Algorithm before selection: `DVSR_OPERATOR_NEUTRAL_OBSERVER_V1`
- Profile: `local-qwen3-8b-awq-dualreplica-v1`
- Graphiti: `0.29.3`
- Horizon/stateful cap: `d=1`, `1`
- Publication: ordered authoritative; speculative writes: `0`
- Validation: fail-closed; fallback: fresh resolve on current authoritative state
- Candidate cuts: CUT-N and nested CUT-D; typed Attributes is excluded for this workload

## Sealed V6 evidence

| Context | Build interval | Node s | Summary s | Edge s | Refinement | Order |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| `longmemeval_s*:3c7e614ecadaf1a6` | 5774875119133 ns | 2617.162 | 1852.796 | 1016.687 | PASS | PASS |
| `longmemeval_s*:68e0c0a3c1784bc0` | 5520666216401 ns | 2659.782 | 1890.137 | 662.168 | PASS | PASS |
| `longmemeval_s*:9b8cbac923a7a2d1` | 7147375073924 ns | 3111.095 | 1970.097 | 1751.754 | PASS | PASS |

The phase values are attribution evidence from the existing provider-free
critical-path reducer.  No provider, database, or sealed artifact was modified
to produce this audit.

## Claim boundary

The valid primary claim is: **DVSR preserves Frozen V6 semantics while changing
only execution timing through dependency-aware preparation, semantic validation,
exact repair/reconvergence, and ordered publication.** Any summary bypass,
predicate pushdown, deterministic materialization, or other work reduction is a
separate extension and cannot be folded into the core result.

Live treatment is unauthorized until certificate adversarial TDD, the complete
development observer, and the offline operator-selection gate pass.
