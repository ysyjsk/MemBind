# V2 Model-Oracle Status Update

This amendment supersedes the identity blocker stated in the earlier
`v2_model_oracle_status.md` report while preserving that report as historical
evidence.

## Current Gate

The operator supplied and bound this deployment fingerprint:

```text
5f5a8400eeaa2f07d167d8b5b7e63d615945a8f54f506e02342840cd4e3fe626
```

It is persisted in
`artifacts/environment/embedding_model_fingerprint.json` with namespace hash
`2b909704578f4793a836f44a92c1fe43e5ce3acfa75058b8b01db7ccf6080392`.
The manifest is intentionally `blocked_unresolved_runtime_config`, with only
`dtype` unresolved. No live embedding cache has been published from this
pending manifest.

## Evidence Classification

Resolved from endpoint or application code: served model ID
`qwen3-embedding-0.6b`, dimension `1024`, exact UTF-8 input keying, and no
Graphiti-side instruction prefix. Official Qwen checkpoint references support
`last_token` pooling and L2 normalization, and retained vectors show effective
unit-norm behavior, but those references are labeled as such rather than as
endpoint-reported deployment configuration.

The actual remote model dtype cannot be established from the available
evidence. Ports `8000` and `8001` initially refused connections, then recovered.
Three consecutive post-restart checks passed: construction reports vLLM 0.26.0
with `max_model_len=40960`, embedding reports vLLM 0.26.0, and a fixed
non-sensitive embedding probe returned 1024 finite values with norm
`1.0000000647885674`. Local Neo4j ports also remained open. This evidence is
persisted in `artifacts/environment/v2_remote_service_preflight.json`; no raw
vector was saved. SSH access to `10.87.5.247` is unavailable, and the endpoint
metadata/metrics still do not expose model dtype. Local
`fp16`/`float16` declarations and the official checkpoint's `bfloat16` value are
not promoted to runtime truth. Required evidence is one of the remote vLLM
launch argv, startup log, or deployed model config plus proof that the process
uses it.

Detailed non-secret provenance is in
`artifacts/environment/embedding_runtime_identity_evidence.json`.

## TDD And Integration Harness

The new manifest and bounded integration contracts are green:

```text
manifest red   artifacts/tdd/operator_fingerprint_manifest_red_017.log
manifest green artifacts/tdd/operator_fingerprint_manifest_green_018.log
integration red   artifacts/tdd/v2_oracle_integration_red_020.log
integration green artifacts/tdd/v2_oracle_integration_green_021.log
full regression artifacts/tdd/v2_full_regression_green_029.log
```

The final full regression ran 217 tests successfully. The V2 integration command is
implemented but remains environment-gated:

```text
.venv/bin/python src/replay_driver.py v2-oracle-integration \
  --attempt v2_oracle_integration_001
```

It performs only M0 capture followed by M0 read-only replay, checks zero replay
LLM/embedding calls, zero cross-encoder calls, fresh Neo4j cleanup, equal graph
and retrieval outputs, and immutable prompt/embedding cache hashes. It does
not start V3's M0-to-M2 smoke.

V3-V6 remain forbidden until the dtype evidence is resolved, the manifest gate
passes, remote services remain healthy, and this bounded integration produces a
success artifact.
