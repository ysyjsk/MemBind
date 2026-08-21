# Block Lifecycle Contract

Every B0/B1 block uses one lifecycle and one construction timer. The lifecycle
is implemented by `block_lifecycle.BlockLifecycle` and is intentionally small:
it records events supplied by the runner and does not start services itself.

## Event order

```text
FRESH_NAMESPACE
BACKEND_PREPARED
SERVICE_READY
WARMUP_COMPLETE
BACKEND_IDLE
FORMAL_START
CONSTRUCTION_COMPLETE
DURABLE_COMPLETE
VALIDATION_COMPLETE
```

`FORMAL_START` is the monotonic `T_build` start and must follow fixed,
disjoint warmup and an idle backend. `DURABLE_COMPLETE` is after all registered
method-caused tasks have reached terminal state and after the last durable
database acknowledgement. The timer interval is exactly
`DURABLE_COMPLETE - FORMAL_START`.

Validation, canonical projection, correctness checks, artifact hashing, seal,
and read-only QA occur after the timer stops. Retry time from a construction
request remains inside the interval. A state transition out of order or a
non-monotonic clock fails closed.

The same contract is used for B0 and B1. A fresh namespace is required for
every block; namespace reuse is an isolation failure, not a performance result.
