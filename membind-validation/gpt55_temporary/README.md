# Temporary GPT API diagnostic lane

This directory contains the short-lived OpenAI-compatible GPT diagnostic path.
The directory name is historical; the active bounded runner is pinned to the
provider alias `gpt-5.4-mini`. It is deliberately separate from shared `scripts/`,
`tests/`, and `src/` trees so an exploratory gateway experiment cannot change
the frozen vLLM validation lane.

Read [WORKPLAN.md](WORKPLAN.md) before running anything.  The lane uses
`/chat/completions`, forwards Graphiti's messages without adding a system
prompt, and writes only diagnostic evidence below this directory's
`artifacts/` tree.  A successful run is labelled
`temporary_api_characterization`; it is never a V3/V4/V5/V6 correctness or
performance pass.

Run the unit tests from `membind-validation/` with:

```text
.venv/bin/python -m unittest discover -s gpt55_temporary/tests -v
```

The tests are intentionally runnable without network access.  Live execution
must first pass the gateway preflight and must use a fresh attempt identifier.

Embedding for this temporary lane defaults to the local `local_bge_m3`
provider in `scripts/local_embedding_adapter.py`.  It uses the upper-level
`/data/predator/ly/Mem` cache with the frozen `BAAI/bge-m3` revision, so the
diagnostic does not depend on the remote embedding vLLM endpoint.

The executable bounded path is
`api_characterization/production_runner.py`. It requires an immutable successful
simple-judge attempt and then performs one fresh structured Chat preflight
before reading the dataset, loading CUDA/BGE-M3, connecting local Neo4j, or
constructing Graphiti. Current results and the black-box latency claim boundary
are documented in `artifacts/diagnostics/gpt54mini_bounded_001_report.md` and
`API_LATENCY_METHODOLOGY.md`.

Before `add_episode`, the production path also derives its `tmp-api-char-*`
Neo4j namespace from both the attempt ID and canonical artifact root, verifies
that the exact namespace is empty, disables raw episode persistence, and
transfers Graphiti/Neo4j/HTTP-client ownership explicitly to the bounded
runner. Trace analysis accepts only closed descendants of the unique
`add-episode` span. These safeguards are covered by the API characterization
suite and are part of the live-run gate, not optional cleanup conventions.
