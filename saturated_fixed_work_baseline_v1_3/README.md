# Saturated Fixed-Work Baseline v1.3

This package is the prospective fixed-work baseline adapter for the MemBind
campaign. It reuses stable v1.2 workload and execution primitives while
keeping the current live gate focused on experiment-critical prerequisites:

* both model endpoints complete requests;
* Neo4j read/write canaries pass;
* the frozen workload, runner, instrumentation, warmup, and idle checks pass;
* every block follows the common lifecycle and uses a fresh namespace.

The machine-readable backend and client contracts are the source of truth for
shared serving behavior. B0 and B1 differ only in execution policy. No
method-specific decode, retry, application worker cap, or backend override is
permitted.

The executable live adapter is
`src/saturated_fixed_work_baseline_v1_3/simple_campaign.py`. The v1.2 run root
and all sealed artifacts remain untouched. The v5 modules provide offline
semantic analysis and passive fingerprint primitives; they do not authorize a
runtime mechanism or a live diagnostic.

Targeted tests:

```bash
PYTHONPATH=saturated_fixed_work_baseline_v1_3/src \
paper-eval-v3/.venv/bin/pytest -q saturated_fixed_work_baseline_v1_3/tests
```
