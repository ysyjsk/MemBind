# MemBind v4 Conflict-Aware Offline Decision

## Decision

```text
STOP_CONFLICT_AWARE_NODE_RESOLVE
final outcome: STOP_V4_NODE_RESOLVE
live authorized: false
```

The registered development scope is `history=07741c45`, sources `0..11`,
speculation distance `1`, NodeResolve only, and global `K=2`. It is not a
formal main-table comparison. The replay made zero network calls and zero
persistent writes.

## Evidence Binding

The decision is derived from `V4_CONFLICT_OFFLINE_REPLAY.json`:

| Evidence | SHA-256 |
| --- | --- |
| replay file | `d003baeca9858cbe91ec11b0d0216741aa2cc32529536bb5616ebe0d412c0834` |
| replay payload | `04caa7d54ec88a89b34f74491ddc26ffa0dde6b30d411cacf7dbfa0eb9e3a17c` |
| A1 audit file | `f7f1355f5be72eec8cd1b161c62acdfe9d92a951bbdd8831923bc25d1f73d1a0` |
| A1 audit payload | `7f85d5c99fd2d3296af26a0d4adcf6bb9382a60c734adb645af1fd0b16b66b75` |
| v3.1 events file | `7b9383010a3d595faaf00548b807e4ae85b85f19d1bfc4415775595a4031bdec` |
| v3.1 block manifest file | `9794ab843b2643ee52171cfad2f24a7cb154b3c94832270d7b211d23caec95a2` |
| v3.1 block manifest payload | `5bbf6fc8fcb87021fea78af5f53079609f051f5f47997d1de36dedcb989b2561` |
| source manifest | `8bcd9fe468bbf471f0a26847b658fc2466df3e14639f05b575a8f207a45a89ec` |
| history arrival trace | `ff5f10b62d375dc7e3cf9963bc34c1277e913a58bf1f8fc29b1f7ad7a89f11a8` |
| execution identity | `823857f46a51e5f65aec196220ff94dcea975aee6ebdd41c765569732ef79231` |
| State-Cut certification | `c0547286e6aab9d475618180e1d291dee2b7105149fee52e13b776d8061113a2` |

The replay verified all `294` sealed v3.1 event rows, rederived all `49`
source timing rows from the event ledger, and required exact equality with
the sealed A1 audit. It also verified the file seal, artifact seal, source
identity, certification identity, and node/edge counts for all 12
PreparedArtifacts. Their artifact hashes, in source order, are:

```text
0  a9aecb4dd39c2d7a6e63e5e73764c92b0932a70215bbd3b30c3ad700988511be
1  aee5a34814215197d7d58d8e897cfb62c39e2d8563ead4d2287055f0ac0b0c50
2  3398305ef2e8532a3d1e797dbc2e18648a6cdd086a4e5f6fbae974c54b531004
3  0f198f04c287684c17c0b8cd4e942f313b266f32be6927c13914235a15d7fdef
4  be2a2265c1fe6bfd60a11ee0aa06c9c0a5b2fbc75dd0f3c06a3bfd4d943737ff
5  670f11cc76380766a5d3b4686306a0fd50d86f5605098cdd9031764ce2549343
6  6fc7c8ac3195d1c045df87b3279ab7bf57ae5b25e06ade907466307fe48d6136
7  06300a178446d2843974a84fecac05a7ec6e35c1c948cf8af044af142941f591
8  d49c22e688f73e8ea3cf6021a4f329b192b06fbcb467f36a89d984a35767450c
9  501fa33f852bbd50d7c7939ba521666d39cd80a5486fb8efa3fcf90d86c44c73
10 9a5fac5648a55fc656269e00ec4c62a420edfbc23f910bb6df63f03d1ccc65d6
11 c729201c6722e5737dfbef147ff59849758f63aa2665bc86eb091cc835046198
```

## Temporal Derivation

For each future source `i`, the replay independently computes:

```text
prepared_lead_ns = publication_durable(i-1) - prepared_durable(i)
potential_opportunity = prepared_lead_ns > 0
```

The observed leads for sources `1..11` are all non-positive:

```text
1  -46177493263     5  -57899607786     9   -3768570733
2  -33636868831     6  -44805076819     10 -23859161462
3  -18451862887     7  -17432615753     11 -66414843092
4  -27043928311     8  -41677816249
```

The first positive opportunity in the canonical 49-source audit is source
`12`, which is outside the fixed `0..11` replay scope. Therefore every
candidate is eliminated before conflict classification.

## P7 Funnel

```text
future PreparedArtifact opportunities   0
after conflict filtering                0
after LLM-required filtering            0
after resource admission                0
after value admission / would launch    0

LOW_CONFLICT                             0
HIGH_CONFLICT                            0
UNKNOWN                                  0
```

The conflict classifier, semantic-mode facts, resource facts, value estimate,
and exact HIT/MISS facts were not reached. LOW, HIGH, and UNKNOWN HIT rates
are `N/A`, not `0%`. No conclusion about predictor selectivity, waste,
interference, or end-to-end performance can be computed from this prefix.

## Read-Only Comparators

`BASELINE_BINDING.json` was verified at file SHA
`488fb6952bebdd2d90cf2074ad72d1c5e1e617bb99d4e1623ff9aff0134fb015`
and payload SHA
`c04443a5ab758c49e34b2f7fa4e311de9e3e47f1e01cb94532a53506eddfa9e8`.
It binds passing U0, A0, and P(C=2) traces, but explicitly records mixed
execution envelopes, so they are inventory evidence rather than a new formal
comparison.

The old blind c01 six-source evidence was also verified read-only. Its block
manifest payload is
`2093f1328ddf858a6329d24954baa0e0255cada3879d94a2c4aeef106889f802`
and its reduction file is
`ad5855feb883ea6f05820386ff6dacb079835b3e071648135babfbee72e9ddab`.
It records `qualified=0`, `launch=0`, `HIT=0`, `MISS=0`, and
`STOP_V4_NODE_RESOLVE`. Since neither the old blind run nor the new replay
triggers speculation, no claim that conflict-aware admission improves HIT
rate, waste, service time, or frontier interference is supported.

## P8 Gate

The first mandatory gate fails:

```text
LOW_CONFLICT opportunities == 0
-> STOP_CONFLICT_AWARE_NODE_RESOLVE
-> STOP_V4_NODE_RESOLVE
```

This is absence of a legal temporal opportunity in the fixed prefix. It is
not classifier rejection, predictor failure, or resource self-starvation.
The plan therefore forbids a `c01_ca` live run.

