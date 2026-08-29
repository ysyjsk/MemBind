# MemBind V7 H1 Architecture Rescue Report

Date: 2026-08-29  
Profile: `local-qwen3-8b-awq-dualreplica-v1`  
Protocol: `R1_R3_PROTOCOL_FREEZE_8B_DUAL_V1.json`  
Frozen algorithm: `V7_FRESH_CONTROL_V1`

## Outcome

The r16 real-Graphiti observer campaign completed all frozen blocks (R1-R2,
R3-A, R3-B) with `treatment_calls=0` and `response_replay_calls=0`. The
opportunity gate selected `NULL`; no incremental live treatment is authorized.

This is an architecture/boundary result, not a scheduler or capacity result.
The selected `node_cosine` seam is not currently a certifiable early-reuse
surface: mutable previous state changes the query/filter/config identity, so the
observer must classify the read as `UNKNOWN` and fail closed.

## r16 evidence

Authoritative seal:

`/data/predator/ly/Mem/experiments/local-qwen3-8b-awq-dualreplica-v1/v7_observer_characterization/r16-observer-2plus6plus6-20260829/SEAL.json`

- `status=SEALED`
- `treatment_calls=0`
- `response_replay_calls=0`
- route events: 1,826
- R1 real Graphiti evidence: true; dependency edge kinds complete
- R1 A7/A9: `UNKNOWN`; `core_assumptions_supported=false`
- R2 reads: 65; requests: 31; demand prediction: `UNKNOWN`; demand truth: `CHANGED`
- R3: two independent six-source blocks completed
- CSP: `null`; exact reconvergence rate: `0`; stable prediction count: `0`
- semantic change amplification: `7.895260057`
- critical opportunity: `UNKNOWN_INCOMPLETE_SEMANTIC_DAG`
- method selection: `NULL`, `authorized=false`

The dangling-edge messages emitted by Graphiti are retained in the run logs;
they are not used as a standalone quality verdict. The sealed semantic and
provenance artifacts are authoritative.

## H1 TDD correction

H1 tests required source-local identity to be independent of mutable state but
scoped to the source identity. The first test run exposed a real collision:
`stable_mention_id()` omitted `source_id`, so identical text from two different
sources produced the same mention key. The implementation now includes
`source_id` in the canonical identity digest.

The fix is deliberately narrow. It does not change Graphiti prompts, routing,
provider work, publication order, or the frozen production V7-FRESH identity.
Because the provider-free reference identity rule changed, the corrected
reference is sealed separately as `V7B_H1_REFERENCE_V2`; the old V7-B contract
remains preserved for audit. The fix prevents cross-source false reuse in the
provider-free reference engine.

## Verification

- H1 targeted tests: `13 passed`
- Full `saturated_fixed_work_baseline_v1_3/tests` suite with the repository
  Python path: `581 passed in 8.48s`
- V7 freeze verifier: `PASS_WITH_EXPECTED_BLOCKER`
- H1 counterfactual campaign:
  `v7_counterfactual/v3-h1-identity-20260829`
- Counterfactual canonical differential: `13/13`
- C1 affected-work fraction: `0.4769230769`
- C0 affected-work fraction: `0.7019230769`
- C1 vs C0 provider-free work reduction: `0.3205479452`

The counterfactual numbers are provider-free work accounting only. They are not
wall-clock speedups and do not authorize treatment.

## Decision and next action

H1 validates the source-local identity contract and closes an engineering
correctness hole, but it does not rescue the live opportunity gate. Because the
real observer still has no certifiable stable node-cosine reads, no meaningful
reconvergence, and no conservative critical-path saving, the preregistered
stop rule is `V7B_ARCHITECTURE_NULL` / `NULL_NO_ECONOMIC_OPPORTUNITY`.

Do not start V7-INCREMENTAL, M2, d>1, B1, or scheduler/lane/future-cap search
under this identity. Any future semantic-boundary change must create a new
algorithm identity and repeat source audit, quality qualification, observer
characterization, and D0/D1 gates.

## Hashes

- `v7b.py`: `3c78a0151099d7a41ac3eeb0bd2deecf192c25e74d6e72ef5ef0090ca0e6bed5`
- `test_membind_v7b_offline.py`: `b4578cd92e6aada48f427077650692ac1cd52e3ef40d7d099318f50ef0120111`
- `run_v7b_counterfactual_campaign.py`: `639a33abb1ad2ee609a14a46907c576a5c4eba440fdc74db6a690150958e724f`
- `V7_FRESH_ALGORITHM_IDENTITY.json`: `e446fa459fd984c3b9a4d6b3698cab0695a956f42e9670b2763be2e4943a3aaf`
- H1 counterfactual summary: `9bd5378032f1def240e592559439e633b82afec062b2b42519b518e96af25949`
- `V7B_H1_REFERENCE_IDENTITY_V2.json`: newly sealed offline-only reference identity
