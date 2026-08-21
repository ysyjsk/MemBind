# v1.3 Test Qualification Gate

The experiment-critical gate is the v1.3 package and the v1.2 modules it
reuses for workload freezing, B0/B1 execution, lifecycle timing,
instrumentation, canonical projection, QA, sealing, and reduction.

The required result is:

```text
sfwb_failures             = []
targeted_failures         = []
repository_failures      = recorded with exact test id/signature
clean_head_failures       = recorded for reproducibility
new_regression_count      = 0
```

Repository-wide failures are recording and comparison evidence. They can be
classified as pre-existing only when clean HEAD reproduces the exact test ID
and failure signature outside the changed dependency closure. A changed
signature blocks the experiment-critical gate. Deleting tests, weakening
assertions, or relabeling a new failure is forbidden.

The historical v1.2 `tests_all_green` field is not consumed by this gate.

## Current repository-wide recording

The unscoped repository command `paper-eval-v3/.venv/bin/pytest -q` was run on
2026-08-21 and stopped during collection with 139 errors. The signatures are
environment/path failures outside this package, including missing `httpx`,
`pydantic`, `pandas`, `graphiti_core`, and package paths. This is recorded
only; it is not classified as a v1.3 regression. The experiment-critical gate
uses the scoped command below and currently has zero failures.

Run the focused gate with:

```bash
PYTHONPATH=saturated_fixed_work_baseline_v1_3/src \
paper-eval-v3/.venv/bin/pytest -q saturated_fixed_work_baseline_v1_3/tests
```
