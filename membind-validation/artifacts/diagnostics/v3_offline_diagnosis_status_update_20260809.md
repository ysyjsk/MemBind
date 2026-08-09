# V3 offline diagnosis status update

Generated: 2026-08-09

## Result

V3 remains at `blocked_v3_structured_output`. The offline diagnosis did not
change the frozen experiment contract and did not authorize a new live smoke.
The first failed attempt remains immutable; M2 never started and M1 remains
forbidden.

The diagnosis establishes four bitwise-identical `2048 -> 8192` truncation
trajectories and an unbounded `extracted_entities` schema array. It does not
establish whether vLLM selected the wrong guided-decoding backend, whether the
backend was active but permitted the loop, or whether another request/runtime
interaction is involved.

## TDD record

The new read-only diagnostic and evidence contracts were developed red-first:

| phase | artifact | SHA256 | result |
|---|---|---|---|
| structured diagnosis red | `artifacts/tdd/v3_structured_diagnostic_red_074.log` | `c7c78426115422428bc0c72e5029799a95b363cd16cb19bf00195dd2d3069122` | import failure before implementation |
| structured diagnosis CLI red | `artifacts/tdd/v3_structured_diagnostic_cli_red_076.log` | `b41bfb5cf4ac517588cc2562cca20dd403224499906ab4ff88c668ff99a06cff` | missing CLI |
| structured diagnosis green | `artifacts/tdd/v3_structured_diagnostic_cli_green_077.log` | `d8f37b148c034e690fce98fd47b30625f7af0ee325ab827dc97a7433b563c937` | 6 passed |
| failure artifact hash red | `artifacts/tdd/v3_failure_report_hash_red_079.log` | `b4334fa6ce75960d0ab73a5e3ea13942d29dcbc7831f9953f38406d8f66baa38` | stale 63-character hash caught |
| request-envelope red | `artifacts/tdd/v3_failure_envelope_red_080.log` | `8ce8d908990cc50867b6f4fb345b97dd79dcf7df4db5713e950206f69d5a9761` | missing safe evidence caught |
| request-envelope green | `artifacts/tdd/v3_failure_envelope_green_081.log` | `8f898ddd5d8185ef16866581286cf428b64b5030a97bb4fb09d23ef5a171d01b` | 4 passed |
| metadata probe red | `artifacts/tdd/v3_vllm_metadata_probe_red_082.log` | `cc9e41822a30368060adeb6e48f17550983b297ece4fc0cb037e3965e5fde8d5` | missing implementation caught |
| metadata probe green | `artifacts/tdd/v3_vllm_metadata_probe_green_083.log` | `30bc2bc7154cb41bc94266db3fd9e2e874e794fd6260c4c5b005a211ff2b996c` | 3 passed |
| timeout-call red | `artifacts/tdd/v3_vllm_metadata_timeout_keyword_red_085.log` | `e5e2f3407a1a6f72e8c06a696bc4ef39310136a175bfe2f7eff3caec40e109c5` | positional timeout bug caught |
| timeout-call green | `artifacts/tdd/v3_vllm_metadata_timeout_keyword_green_086.log` | `30bc2bc7154cb41bc94266db3fd9e2e874e794fd6260c4c5b005a211ff2b996c` | 3 passed |
| upstream contract green | `artifacts/tdd/v3_vllm_upstream_contract_green_089.log` | `a5f51aec11effc659f5b34d584bd77eeedc3592a1d8aed82c2f353a5cf920c36` | immutable v0.26.0 source contract |
| focused diagnosis green | `artifacts/tdd/v3_offline_diagnosis_focused_green_091.log` | `d3ab57fb9149782c22eb35ab2a324d7c0ae4b90f8bfdb2ba40856d7754f8fdec` | 55 passed |
| state-contract green | `artifacts/tdd/v3_offline_diagnosis_state_green_093.log` | `cd34e1d6738ceb531d10a994fe1d818d61b1d65478179b6813adb62369faee42` | 23 passed |
| full mainline green | `artifacts/tdd/v3_offline_diagnosis_final_full_regression_green_094.log` | `1b29707e8df60c87d7d76033b77468e4ec19c9f47fec66273b8359875fc23a8b` | 234 passed |

The full regression command was:

```text
.venv/bin/python -m unittest discover -s tests -q
```

No test called `/chat/completions`, used a GPT provider, or mutated Neo4j.

## Service evidence

The corrected metadata probe artifact is
`artifacts/environment/v3_vllm_metadata_probe_20260809_attempt02.json`, SHA256
`c78a495739533515c6d871897e5d287bf75dbf01c108b389a3a20f647b38f348`. Its four
fixed read-only endpoints all timed out after five seconds. The earlier
attempt01 artifact is retained as an instrumentation failure because it used a
positional urllib timeout; it is not used as service evidence.

The upstream vLLM contract artifact is
`artifacts/environment/vllm_0_26_structured_output_contract_20260809.json`,
SHA256
`91e9adb97547e36c7113ba9e60e790b8703fed5b6fb96ac26f4b37f3552b3d83`. Its
deployed-backend field is intentionally unresolved.

## Next allowed action

Obtain sanitized construction-service argv/startup-log or enabled `/server_info`
evidence and a frozen-protocol compatibility probe for the actual extraction
schema. Only after a service-side correction is demonstrated under the frozen
request, or after explicit protocol-deviation approval, may a new V3 attempt
start. The Graphiti prompt/schema/decoding policy/model/budgets/retry count must
not be changed to manufacture a pass.
