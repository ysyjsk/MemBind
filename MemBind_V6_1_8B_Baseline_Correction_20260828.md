# MemBind 8B Baseline and Method-Boundary Correction

Date: 2026-08-28  
Profile: `local-qwen3-8b-awq-dualreplica-v1`

## Correction

Earlier experiment notes incorrectly used `NATIVE_PARALLEL`/B1 as the Native headline.
That arm runs complete episodes concurrently and may change state evolution and durable
publication order. It is therefore a relaxed-order performance ceiling, not a semantics-
preserving Native comparator.

The formal headline baseline is now `B0/NATIVE_SERIAL` (`native-serial-dual`). B0 uses the
same two Qwen3-8B endpoints, GPU placement, Embedding service, Neo4j instance, workload,
decoding, cache reset/warmup protocol, and public platform manifest as MemBind. It completes
each stateful update and durable publication in source order.

## Frozen research question

`MemBind-Core` asks whether execution timing alone can improve construction time:

> Keep Native's computation semantics, required work, state evolution, and durable publication
> order unchanged. Execute only certified dependency-free PREPARE work early, overlap it with
> authoritative NATIVE work, and use exact replay plus ordered publication. Does this accelerate
> construction relative to B0?

The primary estimand is:

```text
speedup_core = T_B0_NATIVE_SERIAL / T_MemBind_Core
```

`B1_RELAXED_ORDER_UPPER_BOUND` is reported separately as a ceiling ratio. It is never used to
claim that Core is ineffective.

## Method boundary

The Core contract permits only dependency-aware prepare/execution overlap, exact certified
replay, and ordered authoritative publication. `summary bypass`, `predicate pushdown`,
grounded/deterministic materialization, and any optimization that reduces, replaces, or changes
Native provider work are `WORK_REDUCTION_EXTENSION` variants. Each extension requires its own
run contract, ablation, work inventory, semantic-quality report, and attribution. Extension
speedups cannot be merged into the Core headline.

## Existing artifact reclassification

Historical files and attempts remain immutable. Their interpretation is corrected here:

| Artifact | Previous label | Correct label | What it can support |
| --- | --- | --- | --- |
| `20597f72b70f`, `fair-p30-three-arm-20260828-r45a` | Native headline | `B1_RELAXED_ORDER_UPPER_BOUND` | Relaxed-order ceiling only; prefix-30 `696.445710877s` |
| `r55a` prefix-16 Native | Native calibration | B1 auxiliary calibration | No B0 headline speedup |
| `r64a` prefix-16 V6.1 | `1.1669x Native-relative` | `1.1669x` vs B1 auxiliary calibration | Directional only; not Core headline |
| `94ee06dee165`, `phasec-p30-v52-work-conserving-edge-20260828-r65a` | compared with Native | V6.1 result without B0 comparator | `705.136007872s`; no formal prefix-30 speedup claim |
| `phasea-p2-shared-20260828-r1` | four-arm smoke | B0/B1 diagnostic smoke | B0 `146.153s`, Core-like V6.1 `102.249s`, preliminary `1.4297x` |

## Fresh anchor and interrupted Core run

The fresh strict B0 prefix-30 run completed after the correction:

```text
attempt: d6e9e240c3ce
run: correction-b0-prefix30-20260828
contract: HEADLINE_B0_DUAL_RESOURCE_MATCHED
semantics: B0_SERIAL_STATEFUL_ORDERED_PUBLICATION
status: PASS
construction makespan: 2636.463018176 s
episodes/publications: 30/30, source order PASS
logical LLM requests: 858
transport attempts: 1732 (0 failures, 0 retries)
prompt/completion tokens: 26,079,362 / 196,654
embedding items: 3394
DB writes: 150
```

The first Core attempt used the same platform/workload and a valid
`MEMBIND_CORE` contract. It was intentionally interrupted at the user's request after
the early prefix had progressed, so it is not a timed comparison:

```text
attempt: a2631b77f1e2
status: FAILED (user interrupt; asyncio.CancelledError)
durable publications: sources 0--2 (frontier events are duplicated in common/frontier channels)
provider events: 96 (83 replay successes, 11 prepare successes, 2 failures before cancellation)
admission events: 290 enqueue, 290 admit, 290 release
partial prompt tokens: 561,680; partial extraction diagnostics: 594
```

Fairness checker v2 passes the B0 contract against the Core contract on all checks, including
same platform/workload/endpoints/decoding/cache, B0 state evolution, source-order publication,
and Core work-preservation boundary. The interrupted Core run therefore provides engineering
evidence only; a fresh Core completion is still required for the primary speedup.

The prefix-2 result is not a formal claim: it is one small-sample diagnostic and must be
repeated at prefix-30 against a fresh B0 contract.

## Required next sequence

1. Do not run additional B1 scheduler, lane, or future-cap autoresearch.
2. Using the current READY 8B dual-GPU profile, create one fresh `native-serial-dual` contract
   and complete the strict B0 prefix-30 freeze. Do not delete or overwrite B1 artifacts.
3. Run the selected `MemBind-Core` implementation at the same prefix/workload and with a fresh
   namespace. The V6.1 contract must declare `method_boundary.id=MEMBIND_CORE`.
4. Run the updated fairness checker. It must verify B0 state semantics, source-order durable
   publication, identical hard resources/workload/decoding/cache, and Core work preservation.
5. Compute only `T_B0/T_MemBind_Core` as the primary speedup. Report B1 separately as an upper
   bound and include logical/transport/token/embedding/DB work to show whether work is conserved.
6. Evaluate summary/predicate/grounding/materialization changes later as named extensions, never
   as part of the Core result.

## Current claim boundary

Until step 2 completes, there is no formal prefix-30 Native headline anchor. The current data
supports that V6.1 can beat B0 on the prefix-2 diagnostic, but it does not establish a full-scale
Core result. The corrected design makes the answer falsifiable: Core either accelerates B0 while
preserving Native work and state semantics, or the primary hypothesis is rejected; B1 cannot be
used to redefine that outcome.
