# A1 protocol amendment — opportunity exposure (development only)

Date: 2026-08-19  
Protocol amendment: `A1`  
Candidate: `c01`  
Representative history: `07741c45`

## Decision being amended

The original c01 six-source run and its sealed STOP artifact remain immutable.
That run is a valid integration/correctness result for the original `0..5`
prefix, but it is not a claim about the full workload's treatment effect.
The untreated, sealed v3.1 feasibility trace shows:

| untreated prefix | potential NodeResolve opportunities |
| --- | ---: |
| `0..5` | 0 |
| `0..11` | 0 |
| `0..19` | 7 |
| full 49-source trace | 22 |

The first opportunity is source `12`. The seven opportunities in `0..19` are
sources `12, 13, 14, 16, 17, 18, 19`. The audit uses
`prepared_lead_ns = publication(i-1) - prepared(i)` and defines an opportunity
as `prepared_lead_ns > 0`; it is calculated before any v4 treatment is
applied.

## Authorized change

One and only one development exposure is authorized:

```text
candidate_id       = c01
protocol_amendment = A1
history_id         = 07741c45
source_count       = 20
source prefix      = 0..19
policy             = IDLE_SLOT_VALIDATED_SPEC
K                  = 2
speculation distance = 1
```

This changes only the development exposure window. It does not change c01's
policy, prompt, schema, model, tokenizer, structured-output backend,
NodeResolve semantics, retry/transport admission, frontier-first ordering,
publication ordering, compile workers (`2`), lookahead (`2`), or bind workers
(`1`). It does not increase `K` or `W`, and it does not create a new candidate
or authorization system.

The formal experiment, if later authorized, must use the complete original
arrival trace. It must not filter arrivals by the opportunity predicate. This
amendment is a development-exposure gate, not a treatment-selection rule.

## Scope and non-retroactivity

- The original c01 six-source STOP and all prior 6/12 sealed artifacts are
  unchanged and are not rewritten.
- The original workplan is not rewritten by this sidecar.
- c02/c03, GPU sweeps, and the formal four-history run are not authorized by
  A1.
- A1 performance is a development trend and cannot enter the formal fair
  main table. P0 currently records `MIXED_ENVELOPES_NOT_FORMAL_COMPARISON`.
- A1 may proceed only after construction vLLM, embedding vLLM, and Neo4j pass
  the existing READY preflight. A failed preflight is a blocked run, not an
  implicit service reconfiguration request.

## Sealed identities

The amendment is bound to the full 49-source v3.1 identity, not to the
prefix-derived hashes produced by a fresh c01 plan:

```text
history_arrival_trace_sha256       = ff5f10b62d375dc7e3cf9963bc34c1277e913a58bf1f8fc29b1f7ad7a89f11a8
source_manifest_sha256             = 8bcd9fe468bbf471f0a26847b658fc2466df3e14639f05b575a8f207a45a89ec
shared_execution_envelope_sha256  = 1bc49eabdc85f51546bc2b5477141887c087bb1fc0a0b26b33d1bf569660be25
execution_identity_sha256          = 823857f46a51e5f65aec196220ff94dcea975aee6ebdd41c765569732ef79231
provider_execution_envelope_sha256= 31f1a8476650767bc391215675924ceed972e10153df02feeaf44eb9fa54e0ee
```

The machine-readable amendment binds the audit file and payload directly:

```text
audit payload_sha256 = 7f85d5c99fd2d3296af26a0d4adcf6bb9382a60c734adb645af1fd0b16b66b75
amendment payload_sha256 = ccaa4fa3313c05c6921f362bd085fc097430ba04f3c29393b99c168143768499
development reference payload_sha256 = ba0b5edabda8c50573df448ab8b5cfb66546d2de2a555ed38342436151464161
```

The JSON sidecar is the admission input for the offline verifier. Any payload,
history, source inventory, arrival-trace, or audit-file tamper must fail
closed before a candidate namespace is created.

## Reference boundary

The sealed v3.1 trace is reused directly for opportunity timing, identity, and
a development-only `0..19` reference. The reference is computed from
`PUBLICATION_DURABLE.timestamp_ns - ARRIVAL.event.timestamp_ns`; the arrival
target in `ARRIVAL.telemetry.arrival_time_ns` is retained as auxiliary timing
evidence. Its 6/12 slices reproduce the existing `PREFIX_REFERENCE.json`
values exactly.
It is not a formal fair comparator. The fresh A1 run therefore reports a
development trend only, while the formal fair table remains gated on one
common live envelope and complete histories.
