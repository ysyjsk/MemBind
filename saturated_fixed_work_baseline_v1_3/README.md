# Saturated Fixed-Work Baseline v1.3

This is the prospective protocol and adapter layer for a new formal campaign.
It deliberately reuses the stable v1.2 implementation while removing two
historical gates:

1. current-campaign resource identity is sufficient; historical provider
   parity is `NOT_APPLICABLE`;
2. test qualification is based on exact clean-HEAD reproduction and
   `NEW_REGRESSION_COUNT == 0`, not a repository-wide zero-failure boolean.

The executable prospective gate adapter lives in
`saturated_fixed_work_baseline_v1_2/src/saturated_fixed_work_baseline_v1_2/v1_3.py`.
The v1.2 run root and all sealed artifacts remain untouched.  No script in this
directory starts a service or an experiment.

Targeted migration tests:

```bash
../paper-eval-v3/.venv/bin/pytest -q \
  saturated_fixed_work_baseline_v1_2/tests/unit/test_p44_v1_3_migration.py
```
