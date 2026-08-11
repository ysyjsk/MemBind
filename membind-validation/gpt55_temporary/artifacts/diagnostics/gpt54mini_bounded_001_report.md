# GPT-5.4-mini Bounded Characterization Status

Date: 2026-08-11 (Asia/Shanghai)

## Outcome

The isolated `gpt-5.4-mini` Chat adapter, local BGE-M3 path, structured-output
gate, bounded runner, C1 tracing bridge, scoped Neo4j cleanup, and production
CLI are implemented. The remote Chat gate is currently blocked: every current
provider request reached an HTTP 403 result.

A post-implementation production-path audit found and fixed namespace,
resource-ownership, raw-content, completion-accounting, and trace-boundary
risks before any successful API run. The hardened runner is now offline-ready;
the external Chat authorization/route remains the only observed blocker.

**No Graphiti episode was executed.** This report contains no Native Graphiti
construction latency or bottleneck result. The available remote timings are
time-to-rejection only.

## Live Evidence

| Check | Result |
| --- | --- |
| Environment-proxy urllib Chat, one request | HTTP 403, 508.06 ms |
| Direct urllib Chat, one request | HTTP 403, 1824.39 ms |
| Direct OpenAI SDK Chat, one request | HTTP 403, 2165.94 ms |
| Direct authenticated `/models` | HTTP 403 |
| SDK automatic retries | 0 |
| Adapter-added system/developer message | none |
| Returned model | unavailable because request was rejected |

The URL was derived from the active Codex provider and explicitly overridden
to `/chat/completions` as authorized. The provider config itself declares the
Responses wire API. Therefore these failures demonstrate that the current
token/IP cannot use the tested Chat and models paths; they do not distinguish a
missing model alias from a broader gateway permission rule.

The alias `gpt-5.4-mini` is provider-specific and is not claimed as an official
OpenAI public model identity. The wire contract follows the official Chat
Completions endpoint shape, while availability and alias mapping remain relay
properties. Wire reference:
[official OpenAI Chat Completions API](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create/).

## Local Embedding Result

The local embedding preflight passed:

```text
GPU                     NVIDIA GeForce RTX 3090 Ti
model                   BAAI/bge-m3
revision                5617a9f61b028005a4858fdac845db406aefb181
device / precision      CUDA / float16
dimension               1024
normalization           enabled; observed norms 1.0, 1.0
local_files_only        true
fresh preflight time    2.141761376 s
```

Artifact:
`artifacts/diagnostics/gpt54mini_bounded_001_local_embedding_preflight.json`.

## Local Neo4j Result

The local non-Docker Neo4j 5.26.0 process is listening only on loopback at
Bolt port 7687. A fresh read-only check loaded the existing `.env` credentials,
verified connectivity, ran `RETURN 1`, and exercised the production
`assert_attempt_group_empty()` function through the actual Graphiti
`Neo4jDriver`. Both checks passed. No credential value was printed or
persisted, and the namespace check performed no graph mutation.

## Post-Audit Hardening

| Risk | Final behavior |
| --- | --- |
| Attempt namespace collision/residue | Namespace binds attempt ID plus canonical artifact root; an exact parameterized count gate rejects any nonzero or malformed result before `add_episode` and before cleanup ownership |
| Raw LongMemEval content retention | `store_raw_episode_content=False` |
| Graphiti/Neo4j/HTTP lifecycle gap | Explicit one-time resource handoff; every tested pre/post-handoff failure closes the owned resources exactly once |
| Cleanup/close failure after successful add | `completed_add_episode_count=1` is preserved; episode and terminal statuses are separate |
| Invalid API wait fraction | Only closed spans descended from the unique `add-episode` root are accepted; unknown parents, cycles, missing endpoints, and root-boundary crossings fail closed |

## TDD Evidence

- Simple judge adapter: **19/19 GREEN**.
- Historical isolated GPT/local-embedding lane: **27/27 GREEN**.
- Bounded lifecycle, live gate, production wiring, namespace ownership, trace
  analysis, local-only Neo4j guard, structured Chat contract, and scoped
  cleanup: **42/42 GREEN**.
- The nested simple-judge plus API-characterization discovery is **61/61
  GREEN**; the three non-overlapping temporary suites total **88/88 GREEN**.
- Native/mainline complete offline regression was rerun after hardening:
  **688/688 GREEN** in 72.671 s.
- Recorded RED evidence covers the missing structured gate, incorrect cleanup
  order, missing production wiring, and missing documentation.

Final log SHA256 values:

```text
simple judge 19/19
907b6595bc76bf96bb858eec5a4efa2410951bb1ab2fd44ff0f32b6b693a0508

API characterization 24/24 (pre-audit manifest-bound baseline)
5fcb132bbf49f1d0a54748a820d2010800c8a8d8eaa2c2a1fd82ee0c87ecd0cb

expanded isolated lane 27/27
3161a30d1ca8b34d7b55fa7db44972ced6d17adf5cab1c4fa1aca36a02e471a4

mainline offline 688/688
0d77aa49136477d3240e2a2215fcd3cdea88d1bc7fba5fd7aa6d444116b3c0be
```

The compact post-audit verification, including current source hashes, test
counts, local dependency checks, security scan counts, and unchanged mainline
hashes, is persisted at
`artifacts/diagnostics/gpt54mini_bounded_002_offline_verification.json`.

The production outer-gate test replaces provider-config loading, dataset
loading, embedding construction, Neo4j construction, and Graphiti construction
with functions that fail if called. A failed HTTP 403 artifact still completes
the blocked checkpoint, proving those dependencies were not reached.

## Executed Production Dry Run

After all audit hardening, the real CLI was rerun with the immutable direct-SDK
failed preflight. This is a zero-network gate check, not a reused success:

```text
attempt_id                           gpt54mini-bounded-20260811-blocked-003
status                               blocked_preflight
http_status                          403
classification                       preflight_artifact_not_successful
live_dependency_construction_started false
mainline_state_advanced               false
```

The final gate binds the preflight manifest, transport, and summary SHA256
values before and after validation. Only `checkpoint.json` exists in that run
directory. No provider config was
read by the production callback, no dataset was opened, no CUDA process was
created, no Neo4j connection was opened, and no Graphiti factory was called.

Checkpoint:
`artifacts/api_characterization/gpt54mini-bounded-20260811-blocked-003/checkpoint.json`
(SHA256 `0bf046843df43af33a097a7147a864423b84df0fec809213c8e2cbd699ac3d4c`).

## Isolation And Security

- The current provider token has zero exact matches in the workspace.
- New artifacts contain no Authorization/Bearer value, raw prompt, raw response,
  proxy address, or credential fingerprint.
- A final exact-value scan covered 45 current `gpt54mini` evidence files and
  the whole workspace: current provider credential matches `0` in the
  workspace and current evidence, current proxy-value matches `0`, and 20
  parsed JSON files contain `0` forbidden raw-content keys.
- Historical 2026-08-08 gateway diagnostics may contain body previews or proxy
  values. They are excluded from current formal evidence and were not reused.
- The Native mainline does not import or execute this temporary lane.
- **mainline state unchanged**:

```text
CURRENT_STATE.json SHA256
8deeadd7e0cf0b894ab7a97646cf1aa07fbf14796c9ac1f8cd1bbaacb4d36a48

Native freeze.json SHA256
3bca97e1f531dbd23584dd02248a0cbed783f2153f3c756880826ea0c48e001c
```

## Blocker And Resume Condition

The current provider token/IP needs access to both the selected
`/chat/completions` path and the model alias, or a different active provider in
the same Codex config must expose a Chat-compatible route. The official OpenAI
Chat Completions reference confirms the wire shape, but it does not establish
the relay-specific `gpt-5.4-mini` alias or this account's route entitlement.
Once that external state changes, create a fresh one-request simple-judge
artifact. The production runner will then issue one fresh JSON-schema Chat
preflight before loading the dataset, RTX 3090 Ti model, local Neo4j, or
Graphiti. Only after both gates pass will it execute exactly one frozen episode
and persist the phase breakdown.
