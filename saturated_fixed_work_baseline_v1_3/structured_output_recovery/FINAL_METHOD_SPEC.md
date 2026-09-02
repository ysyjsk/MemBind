# Final Method Spec

Method identity: `MEMBIND_RESOURCE_CREDIT_V1`.

Speculative admission uses the deterministic per-pool formula
`future_credit = min(dependency_ready_future_count, max(0, certified_capacity - active_physical_requests - authoritative_reserve), token_credit when authoritative)`. Future work is lazy and dependency-ready; P0 authoritative work has priority, publication remains ordered, and `NO_RESUME_FORMAL_ATTEMPT` is frozen. The former `lookahead=2/future_cap=1/native_future_quota=0` policy is retained only as `MEMBIND_FIXED_2_1_0_ABLATION`.

Status: `FINAL_CANARY_AUTHENTICATED`; engineering canary passed and formal campaign is ready for a fresh sealed manifest.
