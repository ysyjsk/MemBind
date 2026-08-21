# Saturated Fixed-Work Baseline v1.3

This is the prospective fixed-work baseline adapter for a new campaign. It
reuses stable v1.2 workload and execution primitives while keeping live
preflight focused on experiment-critical checks:

* both vLLM endpoints complete requests;
* Neo4j read/write canaries pass;
* workload, runner, instrumentation, fixed warmup, and backend idle pass.

The simplified execution path does not collect or gate on resource forensic
fields. The old v1.2 resource modules and sealed root remain untouched as
historical data only.

The executable live adapter is
`saturated_fixed_work_baseline_v1_3/src/saturated_fixed_work_baseline_v1_3/simple_campaign.py`.
The v1.2 run root and all sealed artifacts remain untouched.

Targeted simplified-path tests:

```bash
../paper-eval-v3/.venv/bin/pytest -q \
  saturated_fixed_work_baseline_v1_3/tests/test_simple_campaign.py
```
