# Root Cause Decision

Status: `L0_COMPLETE` (diagnostic-only evidence; no response content enters construction).

Raw run root: `/data/predator/ly/Mem/run/local-qwen3-8b-awq-dualreplica-v1/root-cause-20260902T24`.

| Requested budget | Response chars | Complete edge prefix | Unique tuples | Duplicate tuple repetitions | Duplicate start repetitions |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 16384 | 472328 | 5950 / 21 | 21 | 0 | 0 |
| 32768 | 996616 | 5950 / 21 | 21 | 0 | 0 |

The 16K response is a byte prefix of the 32K response: `True`.
Both responses exhaust their requested completion budget and stop inside an edge string. The evidence mechanically supports `UNBOUNDED_ARRAY_RUNAWAY_LENGTH_TRUNCATION`.

This audit is a root-cause artifact only. No decoded prefix or salvaged edge is eligible for construction.
