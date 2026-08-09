# V3 restored construction-service status

Generated: 2026-08-09

## Classification

```text
service_restored_backend_config_unavailable
```

The construction vLLM service is reachable again over the direct private-LAN
route. This removes a service-availability condition, but it does not remove the
current V3 structured-output evidence gate.

## Read-only live result

The existing credential-redacting metadata probe was run after its four focused
safety tests passed. The probe used only `/version`, `/v1/models`,
`/server_info?config_format=json`, and `/health`; the generation endpoint was not called.

```text
artifacts/environment/v3_vllm_metadata_probe_20260809_attempt04_restored.json
SHA256 fcbca01fa86592350c43c46fa39debf2d387554041a067b9cc53b78dd2b51bfd
```

Observed service metadata:

| endpoint | status | retained conclusion |
|---|---:|---|
| `/version` | 200 | vLLM 0.26.0 |
| `/v1/models` | 200 | qwen3-32b-fp8, root `/home/lhx/liuyi/models/Qwen3-32B-FP8`, max context 40960 |
| `/server_info?config_format=json` | 404 | no runtime config exposed |
| `/health` | 200 | service healthy at probe time |

The route contract passed with private-target proxy bypass. The artifact
contains no API key, Authorization header, response body, or environment dump.
`/server_info` remains 404, so `server_config_available=false` and the selected
structured-output backend/config remain unknown.

A new non-interactive SSH check was also rejected before any remote command ran
with `Permission denied (publickey,password)`. This machine therefore still
cannot read the model host's process argv or startup log.

## Observability boundary

The other standard read-only vLLM 0.26.0 HTTP surfaces do not close this gap:
health, load, models, metrics, and OpenAPI schema expose service health,
scheduling, model identity, or static API contracts, not the configured or
initialized structured-output backend. A response `system_fingerprint` also
does not prove those structured-output settings.

If the process is configured with a fixed backend, sanitized argv/startup
configuration can establish that configured value. If it is configured as
`auto`, the evidence must additionally identify the request-selected and
engine-initialized backend, for example with sanitized fields:

```text
request_id
schema_sha256
configured_backend
request_selected_backend
manager_backend_class
```

No prompt, schema body, model response, credential, or raw environment is
needed.

## Gate decision

The action scope remains `evidence_collection_only`. Service health is not
evidence that the deterministic truncation was corrected. Consequently:

- the frozen public-path compatibility probe was not rerun;
- `v3_smoke_003 remains forbidden`;
- V4, V5, and V6 remain forbidden;
- Graphiti prompt/schema/model/decoding/budgets/retries remain unchanged.

The next allowed action remains collection of sanitized model-host argv,
startup log, `/server_info`, or equivalent runtime backend evidence. After that
evidence is reviewed, a contract-preserving service correction may be validated
through the unchanged public path. Otherwise explicit protocol-deviation
approval is required.

## TDD evidence

```text
metadata safety contract: artifacts/tdd/v3_restored_service_metadata_contract_green_132.log
SHA256 feabb4add9e2ca3374e538560634d1ec97dc4f4ce89aab12f3d726e36ea9b2f6

state synchronization red: artifacts/tdd/v3_restored_service_state_contract_red_134.log
SHA256 c7e7902ba552477e7202fef06580bec24d707d9e23aa030730244167952b74f6
```

Focused green and full-regression evidence are added to `CURRENT_STATE.json`
only after those runs complete.
