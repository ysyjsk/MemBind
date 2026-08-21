# MEG Metadata Non-Interference Audit

STATUS: PASS

This provider-free audit separates the production semantic data plane from the observability metadata plane.

## Data Plane

Production semantic inputs, Graphiti entity/edge objects, candidate computation, DB mutation payloads, structured-output schemas, request ordering, and admission policy do not consume causal metadata.

## Metadata Plane

The opt-in `causal_metadata_provider` reads task-local operator context and emits bounded request telemetry. `prompt_name` is retained as request telemetry and RequestSpan metadata only.

## Checks

- `causal_provider_is_opt_in`: PASS
- `metadata_is_context_local`: PASS
- `metadata_emits_telemetry_only`: PASS
- `metadata_not_added_to_graphiti_prompt`: PASS
- `metadata_not_added_to_graphiti_objects`: PASS
- `metadata_not_used_for_candidate_computation`: PASS
- `metadata_not_used_for_db_mutation`: PASS
- `metadata_not_used_for_schema`: PASS
- `runtime_builder_only_injects_provider`: PASS
- `request_runtime_ast_parses`: PASS
- `graphiti_runtime_ast_parses`: PASS
- `live_runtime_ast_parses`: PASS

Unknown or absent metadata remains `OPAQUE`; no timing, completion order, or function-name inference is permitted.
