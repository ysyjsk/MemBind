# MemBind Saturated Fixed-Work Baseline v1.2

This is the isolated TDD implementation of the v1.2 workplan. All code, tests,
and artifacts for this protocol remain below this directory. Existing v5, v4,
S5, APC, and prior baseline artifact roots are read-only dependencies.

Protocol tests use the paper-eval interpreter:

```bash
../paper-eval-v3/.venv/bin/pytest -q --basetemp=/dev/shm/sfwb-protocol
```

Live Graphiti commands use `membind-validation/.venv` through `scripts/`:

```bash
scripts/preflight.sh --run-root artifacts/<run_id>
scripts/run_qualification.sh --run-root artifacts/<run_id>
scripts/run_main.sh --run-root artifacts/<run_id>
scripts/run_qa.sh --run-root artifacts/<run_id>
scripts/build_report.sh --run-root artifacts/<run_id>
```

`preflight` binds the green test summary, service canaries, fixed disjoint
warmup, two idle observations, a strict 60-second 1 Hz six-source sampler,
physical resource identity, and the base run manifest. L1 runs B0-A, B0-B, and
B1 on the exact 12-episode prefix. L2 rehearses `07741c45` in an isolated root.
L3 follows the frozen eight-block order, skips already sealed blocks, preserves
terminal failures, and advances attempts without overwriting evidence. L4
derives exactly eight read-only namespaces from the verified formal seal and
emits exactly 32 QA rows without construction.

`build_report.sh` alone performs L5. It requires eight valid construction rows
and 32 QA rows, independently reduces sealed evidence twice, requires identical
outputs, writes and verifies `FINAL_SEAL.json`, and only then creates
`SATURATED_FIXED_WORK_BASELINE_V1_2_COMPLETE`.

TDD observations are append-only in `tdd_evidence.jsonl`. Corrections never
rewrite observations; a verifier accepts only later self-hashed amendments that
bind the exact RED and GREEN line hashes. No live stage may run unless the
self-hashed `test_summary.json` verifies the journal, lists every required
RED/GREEN stage, and reports `tests_all_green=true`.

The current development run is intentionally stopped at L0 because provider
physical identity is unavailable and the two upstream full suites have
clean-HEAD evidence inconsistencies. The immutable STOP can be superseded only
after all three self-hashed physical resource files pass both the historical
parity and live resource gates. `preflight` then creates
`STOP_SUPERSEDED_BY_RESOURCE_RECOVERY.json` with byte hashes for the old STOP
and every resource input; later tampering reactivates fail-closed blocking.

An active or invalid STOP never authorizes qualification, rehearsal, formal,
QA, report, final seal, or completion artifacts.
