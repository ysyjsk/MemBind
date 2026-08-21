# Real MEG Runtime Lineage Audit

STATUS: STOP_REAL_RUNTIME_SEMANTIC_LINEAGE
AUDIT_STATUS: NOT_CERTIFIED

The qualified seam was reached and production semantic work began. The capture then failed during source 0 bind. Two successful production LLM requests are present, but their durable request events contain no semantic operator, subrequest role, or prompt name fields. Those associations are therefore OPAQUE.

Semantic operators before failure: 3
Request spans before failure: 2
Opaque lineage count: 0
OPERATOR_READY count: OPAQUE

The underlying bind error was `bind_failed`; Graphiti 0.29.3 reported missing target entities while resolving edge persistence. This is recorded as an incomplete production semantic trace, not as a capability-proxy failure.
