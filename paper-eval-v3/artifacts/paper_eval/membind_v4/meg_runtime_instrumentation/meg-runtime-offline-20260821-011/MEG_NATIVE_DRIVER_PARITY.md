# Native Graphiti 0.29.3 Backend Contract Parity

STATUS: `PASS_NATIVE_GRAPHITI_0293_BACKEND_CONTRACT_PARITY`
REVISION: `meg-runtime-offline-20260821-011`

Historical provider-free vertical-slice artifacts remain unchanged and are labeled `OFFLINE_FIXTURE_PARITY_GAP`; this bundle is the first native-driver-shaped qualification.

## Contract

The only accepted isolation contract is fixed Neo4j database `neo4j` plus fresh Graphiti `group_id` `fresh-graphiti-group`. Neo4j 0.29.3 inherits `GraphDriver.clone()`, which returns the same driver; `with_database()` is not used by the experiment.

## Gates

- `native_clone_fixed_database_fresh_group_contract`: PASS
- `native_optional_capability_shape`: PASS
- `native_provider_unchanged`: PASS
- `native_shaped_operator_ready`: PASS
- `native_shaped_passive_equivalence`: PASS
- `native_shaped_request_lineage_100_percent`: PASS
- `native_shaped_vertical_slice_commit_publication`: PASS
- `search_fallback_branch_unchanged`: PASS
- `transaction_fallback_unchanged`: PASS
- `zero_shadow_and_external_services`: PASS

Decision: `GO_RETRY_REAL_MEG_OBSERVE_0_2`

No live service, model, embedding provider, or database was contacted while producing this artifact.
