# Fixed policy to resource credit

The headline Core identity is now `MEMBIND_RESOURCE_CREDIT_V1`. The legacy
`lookahead=2, future_cap=1, native_future_quota=0` implementation remains
available only as `MEMBIND_FIXED_2_1_0_ABLATION` and its old artifacts are
immutable.

| Field | Classification | Meaning |
| --- | --- | --- |
| `certified_capacity` | `PLATFORM_DERIVED_PARAMETER` | Frozen provider/semaphore capacity for one physical pool. |
| `bounded_future_request_cost` | `METHOD_INVARIANT` | Maximum structured-output envelope used for conservative admission. |
| `active_physical_requests` | `RUNTIME_STATE` | Calls currently admitted and not terminal. |
| `authoritative_reserve` | `RUNTIME_STATE` | Capacity reserved for the current ordered frontier. |
| `request_credit` / `future_credit` | `METHOD_INVARIANT` | Deterministic formula outputs; no quality or latency feedback. |
| `lookahead`, `future_cap`, `native_future_quota` | `ABLATION_ONLY_CONSTANT` | Retained solely for fixed-policy replay. |
| latency predictors, EWMA, magic thresholds | `REMOVED_HEURISTIC` | Not used by the resource-credit method. |

Future tasks are materialized lazily from the dependency-ready queue only when
the current snapshot reports positive credit. Publication remains source
ordered and authoritative work has priority over future work.
