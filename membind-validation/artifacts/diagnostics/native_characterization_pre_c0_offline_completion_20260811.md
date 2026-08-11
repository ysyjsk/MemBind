# Native Graphiti Characterization: Pre-C0 Offline Completion

## Scope

This checkpoint completes the offline work required before the bounded C0 live
canary in `native-characterization-v1.1`. It does not authorize or report a C0
live result, and it contains no prompt, response, credential, or held-out data.
No construction vLLM, embedding vLLM, Neo4j, SSH, remote file, or external
network request was performed while producing this checkpoint.

## Current Authority

```text
current_stage=NATIVE_CHARACTERIZATION
status=native_characterization_offline_only
current_action_scope=native_characterization_offline_only
stage_progress.native_characterization=c1_qualified_c0_dry_run_pass_waiting_for_services
authorized_live_actions=[]
live_h0_candidate_authorized=false
service_admin_authorized=false
next_allowed_action=operator_start_vllm_then_authorize_c0
```

`CURRENT_STATE.json` SHA256:
`8deeadd7e0cf0b894ab7a97646cf1aa07fbf14796c9ac1f8cd1bbaacb4d36a48`.

## C1 Instrumentation Qualification

The lifecycle and semantic-parity contracts are 34/34 GREEN. The predeclared
five-pair alternating A/A qualification observed these paired overheads:

```text
pair_1=2.5883%
pair_2=1.3171%
pair_3=0.8533%
pair_4=1.5589%
pair_5=-0.3687%
median_paired_overhead=1.3171%
classification=clean_pass
semantic_parity=true
```

Evidence:

```text
artifacts/tdd/native_characterization_c1_lifecycle_green_20260810.log
sha256=b87af35690eaba9143b463bea3a4afb5beb2132437ca3d3d141cf0fc8222c405

artifacts/tdd/native_characterization_c1_aa_qualification_20260810.json
file_sha256=3465a1e3b5a340debe53008111f4391376d7e28e76b7ec4941cbade2374ba328
payload_sha256=6918fc91ddfdd7d3b47f28079814138bf79581ac1633963e0a15508c02ab3967
```

## U0 Freeze And C0 Dry-Run

The U0 factory uses pinned upstream Graphiti `0.29.3` at commit
`021d3a57d511f21b10adaf7fa923bd5c1fce5e9d`, the raw upstream embedder and
reranker, no project stabilizer, and no prompt or embedding cache. The C0
dry-run selected exactly one bounded calibration episode and performed no live
request.

```text
history_id=07741c45
source_sequence=0
graph_namespace=nc-c0-d620535ccf0f0f43
run_id=c0-d620535ccf0f0f43
live_request_performed=false
```

Frozen artifacts:

```text
artifacts/native_characterization/freeze.json
file_sha256=3bca97e1f531dbd23584dd02248a0cbed783f2153f3c756880826ea0c48e001c
payload_sha256=94a08bb27fc3f49ace7de61a706ca80337347407c3f4c1ec7832859a3a1f36cc

artifacts/native_characterization/phase_map.json
file_sha256=afdfd18d17e285fe5b23d9ba8eed2cb893ddabb71723259947a3e7317bd72f31
payload_sha256=968750f50c68794f27d087a3c39f827e3f68b29c6d5b63ea5cc60f7d47ff242e
```

The freeze file remained byte-identical after the qualification and evidence
state transitions.

## Offline Regression Evidence

The regression bound into `CURRENT_STATE.json` is:

```text
artifacts/tdd/native_characterization_pre_c0_final_full_offline_green_20260811.log
tests=682/682 GREEN
sha256=9055ce143575d4262ac04c982a3faa92c699784772d746c2a3a89f462f5a0824
```

An additional full discovery run after the final state write covered the
finalization code and the repository's real deny-all state:

```text
artifacts/tdd/native_characterization_pre_c0_post_state_full_offline_green_20260811.log
tests=688/688 GREEN
sha256=ec873654927bfd9ee553c540c867f885d236bdddee782f316adf1e9b7a92ac38
```

The sensitive-value scan covered 31 changed/new files and the new evidence
logs. It found no persisted high-entropy value from the local `.env`, no bearer
credential, and no private-key material. The low-entropy local database
placeholder was excluded from substring matching because it also occurs in
identifier names and explicit test fixtures.

## Interpretation And Stop

This checkpoint is engineering qualification only. It provides no Native
construction latency breakdown, dependency opportunity measurement, online
freshness/backlog result, naive-parallel result, or systems-paper claim. E1-E4
have not started.

The next step requires the operator to start the pinned construction and
embedding vLLM services. After that, a separate tested state transition may
authorize only `NATIVE_CHARACTERIZATION/native_characterization_c0_live_only`.
No health check or C0 request is authorized by this report.
