# GPT-5.5 temporary diagnostic lane

This directory contains the short-lived LabForge/OpenAI-compatible GPT-5.5
diagnostic path.  It is deliberately separate from the shared `scripts/`,
`tests/`, and `src/` trees so an exploratory gateway experiment cannot change
the frozen vLLM validation lane.

Read [WORKPLAN.md](WORKPLAN.md) before running anything.  The lane uses
`/chat/completions`, forwards Graphiti's messages without adding a system
prompt, and writes only diagnostic evidence below this directory's
`artifacts/` tree.  A successful run is labelled
`gpt55_temporary_diagnostic`; it is never a V3/V4/V5/V6 correctness or
performance pass.

Run the unit tests from `membind-validation/` with:

```text
.venv/bin/python -m unittest discover -s gpt55_temporary/tests -v
```

The tests are intentionally runnable without network access.  Live execution
must first pass the gateway preflight and must use a fresh attempt identifier.

Embedding for this temporary lane now defaults to the local `local_bge_m3`
provider in `scripts/local_embedding_adapter.py`.  It uses the upper-level
`/data/predator/ly/Mem` cache with the frozen `BAAI/bge-m3` revision, so the
GPT-5.5 diagnostic does not depend on the remote embedding vLLM endpoint.
