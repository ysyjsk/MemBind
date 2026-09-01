# Scheduler Problem Audit

Status: `AUDITED_FIXED_GUARD_RETAINED`.

The implementation exposes P0 `NATIVE_FRONTIER` and P2 `FUTURE_PREPARE`; P1 remains disabled because source distance is not a direct-consumer proof. Provider admission is weighted by request tokens and bounded slots. Native guard drains active future work at the non-preemptible boundary.
