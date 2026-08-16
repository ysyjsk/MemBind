# S5 M* Qualification Result Report

Date: 2026-08-16

Run ID: `s5-mstar-20260816-002`

## Outcome

The fresh, isolated M* retry completed and its independent postprocess produced
the canonical scientific result:

```text
canonical verdict                  PASS
scientific outcome                 PASS
episodes prepared                  49/49
episodes published                 49/49
publication order                  0..48
lost / duplicate                   0 / 0
direct invariant violations        0
hidden fallbacks                   0
configured prepare concurrency     2
max active prepare                 2
max active bind                    1
prepare overlap observed           true
failure envelope                   absent
```

The canonical result payload SHA256 is
`a6ff9d630f7d273c3381a3628b7190fff4635738a1b5142a5c037b24f8484cb3`.
Its file SHA256 is
`045e2ed00f3668767c0ac7267cadb85a6761d7f64913df27a9ca264b6581388c`.
An independent call to `verify_s5_mstar_result()` accepted the artifact.

## Execution Summary

The run used the frozen S5 M* production path and execution envelope:

```text
history                           07741c45 (DEVELOPMENT_EXPOSED)
episode count                     49
namespace                         pev3-s5-mstar-20260816-002
construction model                qwen3-32b-fp8
vLLM                              0.26.0
max_model_len                     65536
YaRN factor                       2.0
structured output                 json_schema
requested max_tokens              16384
embedding model                   qwen3-embedding-0.6b
production core identity          49be353d60a6851f762a12dd5c6aadd7ddffb13cad32add212553ed4c5038f00
production identity               9fac15005ea521863882b72a46bec6c2aae9ceb611f9f68842ae04232b9d433c
source manifest                   9de10ac1f3e559c9dc3f6e518410307f401de99004a26f70cbd33dcc189ba08b
```

The source-8 bind was the historical failure point. In retry-002 it completed
after 558.18 seconds and was published normally, after which sources 9 through
48 also completed. One upstream structured response was logged as an
unterminated JSON string and was handled by pinned Graphiti's existing retry
path. The production result still records `fallback_count=0`; no parser,
schema, retry count, prompt, completion cap, or request behavior was changed.

Measured on this bounded smoke run:

| Metric | Value |
|---|---:|
| Arrival-to-final-publication makespan | 2,126.335 s |
| Successful goodput | 0.02304 episodes/s |
| Bind median / p95 / max | 23.392 / 111.869 / 558.183 s |
| Post-return stale-window median / p95 / max | 5.011 / 9.832 / 106.563 ms |

These values qualify the mechanism on one exposed history. They are not an S6
concurrency selection result and are not a paper-level performance estimate.

## Test-Driven Development Evidence

The failure-diagnostic repair followed RED, minimum GREEN, related regression,
and fresh full-offline regression before the live retry:

| Gate | Result | JUnit SHA256 |
|---|---:|---|
| Intentional RED: causal attribution/source closure | 3 expected failures | `eb3165532e1a3045a10a8f8c18ee811dd2ec1d9c9cca3f92e266fcb9ed3ea24d` |
| Focused GREEN: causal attribution/source closure | 34 passed | `235eeb33365454cb7e5fd97761cc19a53a75968615ff399e6d720a67158a5622` |
| Focused GREEN: complete diagnostic chain | 97 passed | `1144a879838a9aaf763a7d1bbd3bad1ca674f6a5b6a1570cbe0f50c9a406ef2d` |
| Related S5 regression | 467 passed | `49ecbd473d3360259df46a8e6f2cdf8d5992c07157844aca86f6d690bd257fff` |
| Fresh full offline regression | 1,442 passed | `68e90aac5cab3d852b4138f21a36b2c3be97f3d1b79b198d2ccce65bad034848` |

The full regression had zero failures, errors, or skips and one upstream
Pydantic deprecation warning. The current-source FX0 qualification additionally
passed all 11 production-path parity fixtures.

TDD logs:

- `logs/TDD_RED_S5_MSTAR_CAUSAL_ATTRIBUTION_AND_SOURCE_CLOSURE_20260816.xml`
- `logs/TDD_FOCUSED_GREEN_S5_MSTAR_CAUSAL_ATTRIBUTION_AND_SOURCE_CLOSURE_20260816.xml`
- `logs/TDD_FOCUSED_GREEN_S5_MSTAR_FAILURE_DIAGNOSTIC_CHAIN_20260816.xml`
- `logs/TDD_RELATED_GREEN_S5_MSTAR_FAILURE_DIAGNOSTIC_CHAIN_20260816.xml`
- `logs/TDD_FULL_OFFLINE_GREEN_S5_MSTAR_FAILURE_DIAGNOSTIC_CHAIN_20260816.xml`

## Identity, Preflight, and Authority Evidence

The fresh identity chain bound the failure-envelope implementation and test as
two of 34 exact source roles. Its source-closure digest is
`0ded2f6c6559153f7305bd49ccc26cc4fa18abe886285bff86e1ccc33a6436ab`.

```text
FX0 runtime-config payload       14309355440e3cbd4f783d199cd9df6f223c63f7abf63705aa9cee5034786757
FX0 parity payload               50924ae5558945cc77fafb7c153577a95197e5071824dc214e9072f04bd091b5
FX0 qualification payload        3ffd05e2b8cbef348ac5ee9d4e4fc48fa0945e2b6e918bbe04f35c33bf5fa272
production qualification payload cfd997c721bbf4ce406e6b3f11f23d075b4d5c11b78acbd0a7aab1872e13aa68
preflight payload                cd58916222bc0658c48df5bf907e77b0f98a7152c074208dbb24f782a78d33d9
single-use authority payload     4a0ec65c323316ad6518bbae57ccd620f749d1fd972aad89cac318c5e9855116
authority consumption payload    690e62783491e23d003fd17e94f7e51738ffe06e457c4b0b9d8e60b8bfd0929c
```

The preflight verified both vLLM services, Neo4j connectivity, and an empty
fresh namespace. The authority was single-use and was consumed exactly once.

## Durable Artifact Index

Primary run directory:

`artifacts/paper_eval/native/runs/s5-mstar-20260816-002/`

| Artifact | File SHA256 |
|---|---|
| Canonical M* result | `045e2ed00f3668767c0ac7267cadb85a6761d7f64913df27a9ca264b6581388c` |
| Attempt result | `9f978e1ad408530611bb75941355bfbf71d82df1b55d714cdbb79b258397628c` |
| Post-observation | `86dcafa98d4fa1ed3e15ca372e59bc91a3977ade4c947fb80e27aaf3cc28e72c` |
| Publication journal | `4c07faf0cb6681450c3076de1eab9a433723bfb238c8003b48143d16d1562c5c` |

Run-local evidence includes:

- `S5_MSTAR_RESULT.json`
- `attempt/manifest.json`
- `attempt/events.jsonl`
- `attempt/checkpoint.json`
- `attempt/result.json`
- `attempt/publication_journal.jsonl`
- `authority_consumption.json`
- `controller/events.jsonl`
- `controller/checkpoint.json`
- `post_observation.json`
- `postprocess/checkpoint.json`

The live console is `logs/s5-mstar-20260816-002.log`.

## Historical Attempt and State Preservation

The failed retry-001 remains immutable and incomplete/non-mergeable. Its
sealed hashes are unchanged:

```text
attempt manifest      0a4c5feb2d5a513f865c8e878a3d9e4b4cc8d88c2179e76f55a75bc29198333d
attempt events        64f028503473befb039be3df0b7f2e51be1f76e287629f5cf7484659deb57b0e
attempt checkpoint    450438f3ca9bbc53f65e4d4f96ae9b7c64a0b081d99ea61fdc4d12a9557a212a
attempt result        b2342832035298ea6ff42cb995b9cc47e95a81cc585bb829994f9ac47811643b
controller checkpoint 8dd43279b845a60dd091fad665c70f01cfd605cf0a7f3f2b48a98467a7988b4e
```

`runtime/CURRENT_STAGE_STATUS.json` also remains unchanged at file SHA256
`3cb7edad4bab3ac6fe961a3d9e8768cbb962cf61cf946cb7e0015d74c0edc26d`.

## Stage Boundary

The result grants `scientific_pass_authorized=true`, but deliberately retains:

```text
next_method_authorized                 false
current_stage_pointer_update_authorized false
pilot_execution_authorized              false
formal_execution_authorized             false
namespace_cleanup_authorized            false
resume_authorized                       false
```

Therefore S5 is scientifically qualified, but this artifact alone cannot start
an S6 live sweep. The next protocol-compliant action is a separate S6 offline
contract and TDD gate for the frozen four development histories and
`C={1,2,4,8}`. Only that chain may issue block-scoped S6 execution authority.
