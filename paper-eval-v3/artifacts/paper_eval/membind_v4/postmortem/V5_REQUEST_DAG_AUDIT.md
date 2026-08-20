# V5 Request DAG Audit

Status: OFFLINE_ONLY

The sealed Q0 trace is diagnostic-only and non-mergeable. v4 remains stopped; no live run or service call was performed.

- History: `07741c45`; sources: `12`; K: `2`
- Requests: `193`; DAG nodes: `205`; edges: `573`
- Edge counts: `{"CONTROL": 242, "DATA": 162, "PUBLICATION": 169}`
- Exact dependency DAG recovered: `false`; unresolved dependency groups: `7`.
- `resolve_extracted_edges` is code-proven parallel across edges, but each edge coroutine may issue sequential dedupe, attribute, and timestamp calls.
- Q0 does not record the per-edge child identity needed to map those internal request chains.
- Prompt names, persistent-state reads, memory versions, vLLM batch membership, and GPU execution width remain `NOT_OBSERVABLE`.

Artifact SHA-256: `cf31e32dfb997aabb4f6eb5420a668efe26671d1a3d23755960f064392634419`
