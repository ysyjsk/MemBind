# v1.3 Test Qualification Gate

The v1.3 implementation is `evaluate_test_qualification` and
`require_test_qualification` in
`saturated_fixed_work_baseline_v1_2/src/saturated_fixed_work_baseline_v1_2/v1_3.py`.
The required result is `NEW_REGRESSION_COUNT == 0`.

## Scope decision

The core gate is the SFWB dependency closure: the v1.3 protocol package and
the v1.2 modules it reuses for dataset freezing, B0/B1 execution, lifecycle
timing, instrumentation, telemetry, canonical projection, QA, sealing, and
reduction.  New tests and every affected targeted/regression test are also
inside this closure.  They must have zero unexpected failures.

The repository-wide suite is a required *recording and comparison* surface,
not permission to silently ignore failures.  Any repository-wide failure is
accepted only if clean HEAD reproduces the exact test ID and failure signature
and the failure is outside the branch change.  A new or changed signature is a
regression and blocks L0.  Thus repository-wide execution is mandatory, while
historical unrelated failures do not become a false SFWB regression.

The currently recorded five paper-eval v4/MSEG failures and eleven
membind-validation Neo4j evidence errors are development evidence only.  They
must remain recorded with their clean-HEAD IDs/signatures; this migration does
not relabel them, delete them, or turn the gate true.  A future campaign must
provide a fresh test summary proving the exact clean-HEAD comparison before L0.

## Required evidence

```text
sfwb_failures                 = []
targeted_failures             = []
repository_failures          = recorded
clean_head_failures          = recorded
exact (test_id, signature)    = required for pre-existing classification
new_regression_count         = 0
```

The old v1.2 `tests_all_green` field remains historical artifact data and is
not consumed by the v1.3 gate.
