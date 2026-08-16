# S5 A0 retry-003 terminal failure and serving-collision report

Date: 2026-08-16

## Verdict

`s5-a0-20260816-003` is a canonically valid fail-closed attempt, not a
successful A0 method smoke:

```text
canonical verdict       VALID_FAIL_CLOSED_NON_MERGEABLE
attempt status          incomplete_non_mergeable
controller status       incomplete_non_mergeable
failure stage           native_execution
failure code            NATIVE_ADD_EPISODE_FAILED
error class             json.decoder.JSONDecodeError
failed source sequence  8
published episodes      8 / 49 (source_sequence 0..7)
post observation        NOT_AVAILABLE
final result            NOT_AVAILABLE
```

The canonical preflight, authority, authority-consumption, durable-attempt,
controller, and progress verifiers all accepted their respective artifacts.
The failure is therefore a valid terminal observation. It is not evidence of
artifact corruption, but its `incomplete_non_mergeable` status forbids use as
the A0 predecessor or as mergeable paper evidence.

## Terminal accounting

The sealed terminal event records:

```text
expected episodes                    49
intent events                        49
durable caller returns               49
committed publications                8
failed treatment                      1  (source_sequence 8)
remaining sources not published      40  (source_sequence 9..48)
configured workers                    1
observed workers                    [0]
maximum active calls                  1
within-run whole-update overlap   false
```

The final `whole_update_interval_overlap_observed=false` field describes A0's
within-run single-worker behavior. It does not negate the cross-run serving
collision disclosed below.

The construction service remained reachable. The retry-003 log records four
native Graphiti structured-output attempts ending in truncated JSON near the
16,384-token completion limit, followed by the terminal
`json.decoder.JSONDecodeError`. No postprocess was started after this native
failure.

## Canonical chain

| Evidence | SHA-256 / internal seal | Canonical result |
| --- | --- | --- |
| `S5_A0_LIVE_PREFLIGHT_RETRY_003_20260816.json` | `1985f60ccd96be10f95cdf391186b21d7a89b545453f60dbc2a405133642df46` | PASS; Qwen3-32B-FP8, vLLM 0.26.0, max model length 65,536, embedding qualified, namespace empty |
| Preflight payload | `d4898f90e462162e134dcd5b9a046fc93abbeaae9409eb08a39915b114329f3d` | bound to retry-003 |
| `S5_A0_LIVE_AUTHORITY_RETRY_003_20260816.json` | `7feaf94dd9095895ef5f99cd59e5a9bc300d3a0f952f7cf306f8973de1677f61` | valid single-use authority |
| Authority payload | `e2df3c821543726074363175fff39e851bf4985526ddbcd1756afe783efe8358` | bound to retry-003 |
| `authority_consumption.json` | `568837fe5af9e9cb22f972e686704015abb1e1cbeb186810bb245dd0f2c2a789` | valid; `further_live_authority=false` |
| Authority-consumption payload | `8abe83b41accf5452b07531ab3c9a3024a2fff34e6ab0a86e3b4508e77c94b9d` | valid |
| Attempt manifest | `515f3f6518322bed3bff79adda91766994471c57e58c0df75ed15f4f6e3c8d32` | valid |
| Attempt events | `c3bfb4d75a908047cf60e9aa98f37c25219b38dc3c16673541107c2f2d2b1bd2` | 107 hash-bound events |
| Attempt checkpoint | `69079b433847ae63ee84487a3b89cbb10d20f1146390d83472fd7f3447c03b99` | terminal; internal seal `0d5493bd16787e7c81047af33114937fe7fbc5035d774032530e3c727c934ea0` |
| Attempt result | `32215ab8bbe509001f2a92a7fb3fba72f003bcf6a56729ce0c6beb3e32dfd230` | fail-closed; internal seal `da91a093ab2275bcfb134a041cfecbad5b64d77591b86939c42c905fcee06cc5` |
| Controller events | `bd4cf00922895bc8dcabb5a5bd84114d7f3b1c2f6225d25cb57d9a9c55ffc5ee` | 6 hash-bound events |
| Controller checkpoint | `44b68aeca106e5ea173033fe5142a058dbeaafb933add23abc03dd98430535a3` | terminal; internal seal `38bca42381b1d098a231b77a9886b3b49a4b652bfbcdfbf2d4b7999100a76ee3` |
| Execution log | `02de6b594521c14c716f1526a17e71501c6c709b5180954c543613aeb58e3ba5` | diagnostic only; not a scientific seal |

The shared A0 runtime config file has SHA-256
`2e92e4e88152ed113b6c923335fd863689f19ffc5c3e01e1cd549379e99f1591`.
The attempt result remains bound to production-core identity
`61e08aef22d1059084120dc18a5937a8512e3f9611d40a22e16bd70828418712`
and native-path identity
`f25141000494a8899a40b87f2bf5fb5e5cb519ab2d480d72973aeaf9e0d9c8cc`.

## Serving-collision disclosure

Retry-002 and retry-003 used different Neo4j namespaces, so their graph data
did not share a namespace:

```text
retry-002  pev3-s5-a0-20260816-002
retry-003  pev3-s5-a0-20260816-003
```

They nevertheless used the same construction serving envelope, frozen runtime
config, production/native identities, and exact 49-source workload. Their live
controller intervals overlapped:

```text
retry-002 authority consumed   2026-08-16 03:11:29.654603378 +0800
retry-003 authority consumed   2026-08-16 03:16:51.566249411 +0800
retry-002 terminal checkpoint  2026-08-16 03:47:59.871857640 +0800
retry-003 terminal checkpoint  2026-08-16 03:53:08.575847760 +0800

minimum proven overlap         31m08.305s
```

Operational observation during the overlap also showed both controller
processes concurrently connected to the same construction vLLM. This
observation is disclosure metadata, not a sealed scientific measurement.

Consequences:

1. Retry-003 latency, service-time, throughput, and queueing values are
   confounded and must not be merged or used for performance claims.
2. Retry-003 independently repeats retry-002's functional failure signature:
   both bind the same input and identities, publish exactly sources 0..7, then
   fail source 8 with `NATIVE_ADD_EPISODE_FAILED` and `JSONDecodeError`.
3. The repetition may guide diagnosis, but neither attempt qualifies A0 and
   neither authorizes P*, cleanup, resume, another live attempt, or a current
   pointer update.

## Regression evidence

The focused verifier/controller regression completed after terminal
inspection:

```text
tests        85 passed
failures     0
errors       0
JUnit        logs/TDD_RELATED_GREEN_S5_A0_RETRY_003_TERMINAL_AUDIT_20260816.xml
JUnit SHA256 81b8bc694a89be6c2bff68e3ebc67c69e25b5bd4888e96a1143e79bdbce4fa9b
```

`git diff --check` passed. `runtime/CURRENT_STAGE_STATUS.json` remains
unchanged with SHA-256
`3cb7edad4bab3ac6fe961a3d9e8768cbb962cf61cf946cb7e0015d74c0edc26d`.
No namespace was cleaned, no live service was started, and no workplan,
freeze, max-token setting, schema, workload, or current-stage pointer was
modified during this terminal audit.
