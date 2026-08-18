# MemBind v3.1 Phase Critical-Path Diagnostic

> Diagnostic-only offline analysis; not a formal performance result.

- observation window: `698779022152 ns`
- queue trace: `c33a9be35d0c5327617cc6769a8fa441b799020af2dc34303ab87deb0bda3f83`

## Phase Exposure

| Phase | Time (ns) | Fraction |
| --- | ---: | ---: |
| `BINDING` | 254124162685 | 0.363669 |
| `BIND_DISPATCHED` | 22094620 | 0.000032 |
| `COMPILE_ACTIVE` | 304471710803 | 0.435720 |
| `NO_SOURCE_ARRIVED` | 140135283983 | 0.200543 |
| `READY_TO_BIND` | 5288165 | 0.000008 |
| `WAITING_FOR_COMPILE` | 18930482 | 0.000027 |

## Diagnostic Findings

- legal-ready duration: `6258028 ns`
- max legal-ready Compile count: `1`
- under-capacity with waiter: `0 ns`
- structural overlap lower bound: `444606994786 ns`
- structural speedup upper bound: `1.5716779770600304`

## Verdict

- admission: `NO_ADMISSION_UNDER_CAPACITY_WITH_WAITER_OBSERVED`
- ready pool: `SINGLE_LEGAL_READY_WORK_ONLY_OBSERVED`
- Bind phase: `BIND_PHASE_NOT_LONGER_THAN_COMPILE_PHASE`
- claim boundary: `DIAGNOSTIC_ONLY_REQUIRES_ALIGNED_FORMAL_CONTROL`
