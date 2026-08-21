# MEG Runtime OBSERVE_ONLY Capture Postmortem

```text
STATUS: STOP_REAL_RUNTIME_SEMANTIC_LINEAGE
RUN_ID: membind-v31-opt-w4-meg-runtime-observe-20260821-001
HISTORY_ID: 07741c45
AUTHORIZED_SOURCES: 0..2
REACHED_SOURCE: 0 compile
SEMANTIC_OPERATOR_COUNT_BEFORE_FAILURE: 1
REQUEST_SPAN_COUNT_BEFORE_FAILURE: 0
TRANSACTION_COMMIT_COUNT: 0
PUBLICATION_COUNT: 0
SHADOW_READ_COUNT: 0
```

## Root Cause

The v3.1 compile path supplies a capability-limited `_LLMOnlyClients` object
that intentionally exposes only `llm_client`. The OBSERVE_ONLY `_ClientsProxy`
used by the captured implementation eagerly accessed `driver` and `embedder`
during construction. It therefore raised before the first production LLM
request was submitted.

This was an instrumentation composition defect, not a provider response,
Neo4j transaction, semantic-lineage ambiguity, or workload result. The run
stopped fail-closed during source 0 compile. It created no persistent write,
transaction commit, publication, or shadow behavior.

## Evidence Binding

```text
CAPTURE_CONTRACT_PAYLOAD_SHA256: dcb589317dab0b364873984def2b781466bf47a6988653b6ce5bf07424f5f00f
FAILED_SEAM_SOURCE_SHA256: 2c437d06ee93baf6d328a18d92f3b894495e4c514f7e125d90208b3a56c36294
FAILURE_PAYLOAD_SHA256: 03fa988c3c8fc622cbb7490eabf7d9535433ce1a5ee5479431b0f68bb0c40913
```

The failed run ID, namespace, and artifact root are non-reusable. A subsequent
source fix handles optional compile-client capabilities and is covered by a
provider-free test that executes the actual Graphiti 0.29.3 `extract_nodes`
function with an LLM-only client. That fix does not authorize a live retry.
