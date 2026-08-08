# V2 Model-Oracle Implementation Status

Status date: 2026-08-08 (Asia/Shanghai)

## Outcome

The V2 local implementation and unit contracts pass. Live V2 capture/replay is
not authorized yet because the remote embedding endpoint does not expose an
immutable checkpoint revision and no operator-supplied deployment fingerprint
is available. `CURRENT_STATE.json` therefore remains at V2 and continues to
forbid V3-V6.

This is a deliberate protocol block, not a network outage. Both remote model
services and local Neo4j were reachable during the check.

## Implemented Contracts

### Embedding oracle

- Immutable namespace header with served model ID, identity kind/value,
  dimension, dtype, pooling, normalization, instruction policy, and input
  transform.
- `endpoint_revision` and 64-hex `deployment_fingerprint` are the only accepted
  identity kinds.
- Served alias, URL, model-root path, vLLM version, and behavior probe cannot be
  used as checkpoint identity.
- Exact single-item UTF-8 keys; no stripping or Unicode normalization.
- `create("x")`, `create(["x"])`, and `create_batch(["x"])` share one item.
- Multi-item `create(["x", "y"])` is rejected.
- Exact vector capture and read-only replay with zero live fallback on miss.
- Namespace mismatch, corrupt/truncated JSONL, unknown records, hash mismatch,
  non-finite values, wrong dimension, and conflicting duplicates fail closed.
- Capture files are exclusively created and cannot be reopened writable.
- Batch vectors are all validated before any item from that owner batch is
  persisted.
- Miss diagnostics contain only input hash, code-point/byte lengths, call shape,
  ordinal, episode/source identity, namespace hash, and prior hit count.

### Runner semantics

- Correctness mode passes the LLM and embedding caches together to the Graphiti
  factory before any live model call.
- Production namespace dimension is fixed at 1024.
- A missing operator identity manifest fails before service checks.
- M1 oracle miss returns `completed_with_divergence` and
  `execution_path_divergence`, with final semantic parity marked not evaluable.
- M2 oracle miss is a correctness failure and sets `blocks_performance=true`.
- Final embedding metrics are refreshed after final retrieval instead of using
  the earlier pre-retrieval snapshot.

### Cross-encoder audit

- The Graphiti cross encoder is wrapped by a transparent runtime counter.
- Successful and failed `rank()` calls are counted before delegation.
- Events store phase, hashes, lengths, passage count, episode key, and outcome;
  query/passage bodies are not stored.
- Warm-up, construction, and final retrieval have separate phase labels.
- Zero calls produce `not_invoked`; any nonzero call produces
  `invoked_requires_capture_replay` and blocks V2.
- An uninstrumented cross encoder cannot masquerade as zero calls.
- The final audit is written only after final retrieval returns.

## TDD Evidence

The relevant red-green evidence is:

```text
RED   embedding_oracle_v2_contract_red_003.log
      649937a13f45bfa867759b3ac6a4a7fb0c79f3f6b7fcbe00fb791f816ab20eb5
GREEN embedding_oracle_v2_unit_green_005.log (30 tests, OK)
      a444cb3b759a4b45a3c9aff1a94835c523d9137b008bd3c0111683f93a0dd5de

RED   embedding_oracle_v2_hardening_red_008.log
      109e1fa4e3d2c61c2add1b3d6afe896a01c56f3fda2e00673746cbcafd240a9d
GREEN embedding_oracle_v2_hardening_green_009.log (33 tests, OK)
      51b0fd7d9aabc3641b75d81dfdc885d41e49d793af229cb308a0714e457bc43a

RED   model_oracle_audit_red_004.log
      e6b4028b45c67b40338fdd6d560c9616ad89ea5697b84f22780659618d9fd73c
GREEN model_oracle_audit_fail_closed_green_013.log (5 tests, OK)
      21716e0ea5a61e77e60f746fc86c31c8fd31f21dc0b6d762850d365c8daa9049

RED   embedding_identity_probe_red_010.log
GREEN embedding_identity_probe_green_011.log (4 tests, OK)

GREEN v2_full_regression_green_016.log (204 tests, OK)
      ed8d4946660a09819574008f1d08d34345e8076554579f5cbc4b76335eaffa2f
```

`python -m compileall -q src scripts` and `git diff --check` also pass.

The full regression intentionally still exercises the historical 64-run
scheduler implementation. The current 72-run contract will replace it only in
V6 after a separate scheduler red test, as required by the active plan.

## Live Readiness Observations

Read-only endpoint metadata observed:

```text
construction served model  qwen3-32b-fp8
construction max_model_len 40960
construction vLLM          0.26.0
embedding served model     qwen3-embedding-0.6b
embedding max_model_len    32768
embedding vLLM             0.26.0
embedding revision         not reported
```

Local Neo4j Community 5.26 was running, and both local ports were open:

```text
HTTP 127.0.0.1:7474
Bolt 127.0.0.1:7687
```

Static inspection of pinned Graphiti v0.29.3 shows construction edge searches
and final `Graphiti.search()` using `EDGE_HYBRID_SEARCH_RRF`; node dedupe uses
direct cosine search. Thus the preregistered expectation is zero cross-encoder
calls. This does not replace the required runtime measurement.

## Blocking Evidence

```text
path    artifacts/environment/embedding_identity_probe.json
sha256  c693905ad3db6d95575a191efa38848f3a4b976606eebff5195d6c42b49276ac
status  blocked_missing_immutable_identity
```

The endpoint returned a served alias and a model-root path, but neither is an
immutable checkpoint fingerprint. The root-level `/version` endpoint confirms
vLLM 0.26.0; `/get_model_config` and `/server_info` return 404 and expose no
revision. The endpoint URL and embedding behavior were intentionally not hashed
and promoted to model identity.

## Exact Unblock Requirement

The operator must provide one SHA256 deployment fingerprint derived from an
immutable manifest that binds the remote embedding deployment, including model
weights or their trusted content hashes, model/config/tokenizer files, and the
vLLM launch configuration. The resulting namespace must be persisted at:

```text
artifacts/environment/embedding_model_fingerprint.json
```

After that artifact is validated, the only remaining V2 work is one bounded
live M0 capture/read-only replay integration with the runtime cross-encoder
audit written to:

```text
artifacts/diagnostics/model_oracle_audit.json
```

V3 must not start before both gates pass.
