# Judge Qualification Final Offline Test Execution

Scope: `JUDGE_QUALIFICATION_ONLY`. These commands were run after the final
gate-hardening RED-to-GREEN change and before creating any live authorization.
All three commands exited with status 0. The raw unittest logs are bound by the
sealed structured reports; this note records the exact invocation without
modifying those logs, whose last non-empty line must remain `OK`.

## Focused

```bash
.venv/bin/python -m unittest -v tests.test_judge_deployment_evidence tests.test_judge_qualification tests.test_judge_qualification_authorization tests.test_judge_qualification_authorization_hardening tests.test_judge_qualification_durability tests.test_judge_qualification_final_gate_hardening tests.test_judge_qualification_formal_deployment_evidence tests.test_judge_qualification_formal_live tests.test_judge_qualification_identity_drift_probe tests.test_judge_qualification_live tests.test_judge_qualification_prelive_gate tests.test_judge_qualification_prelive_semantics tests.test_judge_qualification_prelive_verifier tests.test_judge_qualification_production_fixture tests.test_judge_qualification_q3_dry_run tests.test_judge_qualification_transport_authorization
```

Result: `79/79 GREEN`, exit code `0`.

Raw log: `artifacts/tdd/judge_qualification_final_focused_sealed_20260813.log`

## Impact

```bash
.venv/bin/python -m unittest -v tests.test_evaluator_registry tests.test_longmemeval_adapter tests.test_qwen3_judge_backend
```

Result: `35/35 GREEN`, exit code `0`.

Raw log: `artifacts/tdd/judge_qualification_final_impact_sealed_20260813.log`

## Q3 Dry-Run

```bash
.venv/bin/python -m unittest -v tests.test_judge_qualification_q3_dry_run
```

Result: `1/1 GREEN`, exit code `0`. The test forces both HTTP paths through
explicit `httpx.MockTransport` instances and asserts five scenarios,
`real_external_requests=0`, and `live_authorization_created=false`.

Raw log: `artifacts/tdd/judge_qualification_final_q3_dry_run_sealed_20260813.log`
