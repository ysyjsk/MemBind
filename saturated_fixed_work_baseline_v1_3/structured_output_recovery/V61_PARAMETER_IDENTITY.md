# V6.1 Parameter Identity

Status: `FIXED_POLICY_SEALED`.

The current HEAD implementation uses `V6_FIXED_POLICY` with `lookahead=2`, `future_cap=1`, and `native_future_quota=0`. These values are bounded-admission safeguards: they limit logical/source exposure and protect the non-preemptible provider boundary. They are not claimed to be optimal and were not selected from official performance results.

P1 is disabled until a direct-consumer dependency edge is proven. Physical admission uses the runtime-derived `CapacityAuthority` and weighted `request_tokens` (prompt residency plus bounded decode reserve). No arrival predictor, service-time predictor, or benchmark-tuned threshold is part of method correctness.

The adaptive search terminal is `VALID_NEGATIVE_ADAPTIVE_RESULT_FIXED_CONTINUES`. Engineering canary authorization remains false until authenticated service preflight completes; formal three-arm authorization remains false.
