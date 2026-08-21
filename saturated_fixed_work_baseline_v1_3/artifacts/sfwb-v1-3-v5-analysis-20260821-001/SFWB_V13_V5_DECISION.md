# SFWB v1.3 V5 decision gate

## Decision

`GO_V5_SEMANTIC_WORK_CONSERVATION` (primary)  
`GO_V5_SERIAL_EQUIVALENT_STATE_CUT` (secondary)

## Evidence

- MemBind adds 61 logical calls and 175 embedding items versus B0-A while producing a 487.613 s makespan close to B1's 482.967 s. This is a realized-work divergence, not evidence of a clean 2.02x scheduling gain.
- B1 removes 71 calls, 504,393 input tokens, and 124 embedding items versus B0-A, while its graph differs from serial and its inversion count is 22. The trace proves branch-shape divergence, not semantic equivalence.
- B0-A versus B0-B establishes a non-zero serial floor of 2 entity-key, 4 edge-key, 6 attribute, 6 temporal, and 4 source-link differences plus 51 input tokens. MemBind exceeds that floor materially in every graph category.
- All blocks have zero direct semantic violations; MemBind has complete publication coverage. These are protocol gates, not outcome-equivalence proof.
- Resource capacity is `NOT_EVALUATED`; no `GO_V5_BACKEND_SATURATED_EXECUTION` is justified and no backend mechanism is implemented.

The next permitted research step is an offline/provider-free specification and qualification of work-plan conservation and serial-equivalent state cuts. A production scheduler, admission policy, K sweep, new live run, SHADOW_READ, and stale-state mechanism remain frozen until those contracts are established.
