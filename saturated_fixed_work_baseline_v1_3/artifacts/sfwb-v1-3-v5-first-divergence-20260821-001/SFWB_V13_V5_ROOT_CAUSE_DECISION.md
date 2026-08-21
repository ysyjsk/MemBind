# SFWB v1.3 V5 root-cause decision

`STOP_V5_FIRST_DIVERGENCE_INSUFFICIENT_OBSERVABILITY`

## What is proven

- The 12 immutable source hashes match between B0-A and MemBind v3.1.
- The request/span telemetry reconstructs `+32 EDGE_RESOLUTION` and `+30 TIMESTAMP` downstream work.
- The extra work is not proven to be duplicate consumption; it is compatible with an earlier legal branch, state/candidate, extraction-input, or batching divergence.
- Both paths reach sealed publication for every source.

## What is not proven

The current sealed inputs cannot establish the first semantic output divergence. B0-A has no prepared/extraction artifact paired with MemBind; neither path provides complete extraction output digests, exact full prompt hashes, candidate identity/order, batch membership, state-version-at-read, resolution decision identity, or effect identity. Final graph differences are intentionally excluded as causal evidence.

## V5 contract gate

Before any mechanism is implemented, V5 must preserve Native Serial operator lineage and partial order, exact extraction input/output identity, batch membership, state-version-bound candidate/resolution decisions, effect identity/cardinality, and durable publication lineage when only execution timing is changed.

No `GO_V5_NATIVE_EQUIVALENT_COMPILE`, `GO_V5_SERIAL_EQUIVALENT_STATE_BIND`, `GO_V5_NATIVE_BATCH_PRESERVATION`, or `GO_V5_SEMANTIC_WORK_DEDUPLICATION` is justified yet. The next step is limited to provider-free observability contract design and fixture qualification; no live run or runtime change is authorized.
