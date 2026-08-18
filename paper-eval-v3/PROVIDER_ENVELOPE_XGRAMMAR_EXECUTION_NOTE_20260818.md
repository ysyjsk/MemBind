# xgrammar Provider Envelope Execution Note

This note is an additive execution record. It does not amend the frozen
methodology, method plan, workload, prompt/schema, or historical attempts.

## Serving identity

- Construction: vLLM `0.26.0`, `qwen3-32b-fp8`, `65536` context, YaRN factor
  `2.0`, xgrammar/json-schema, APC and chunked prefill, GPU budget `0.75`.
- Embedding: vLLM `0.26.0`, `qwen3-embedding-0.6b`, pooling/bfloat16,
  `32768` context, GPU budget `0.15`, operator fingerprint recorded in the
  envelope.
- Restricted startup-log snapshot hashes are stored in
  `artifacts/paper_eval/membind_v31/PROVIDER_EXECUTION_ENVELOPE_XGRAMMAR_20260818.json`.
- Provider envelope SHA256: `94efa42151cf272c0fd254b43cfdd897d80272659a31786d7c3b430b93208ed7`.

## Attempt boundary

- `membind-v31-feasibility-20260818-002` stopped before any live request
  because the paper-eval virtualenv lacked `graphiti_core`; it is
  `FAILED_NON_REUSABLE` with no block checkpoint and no namespace mutation.
- `membind-v31-feasibility-20260818-003` is the only active attempt. It uses
  the already-qualified Graphiti environment at
  `membind-validation/.venv`, a fresh attempt root, and the exact frozen block
  0 source/arrival identity.
- Long runs should use `scripts/launch_membind_v31_feasibility_tmux.sh` so the
  interpreter preflight cannot be skipped.

## TDD evidence

- Provider and single-history focused tests: `21 passed`.
- v3.1 related offline suite: `264 passed, 1 warning`.
- Full repository collection still has pre-existing S5 dependency/path errors
  (`graphiti_core` absent from the paper-eval venv and `tests.*` imports); those
  are outside this lane and are not counted as a v3.1 regression.

The live attempt remains a feasibility gate, not a main-table result, until
all 49 sources reach the durable publication boundary and its artifact
verifier passes.
