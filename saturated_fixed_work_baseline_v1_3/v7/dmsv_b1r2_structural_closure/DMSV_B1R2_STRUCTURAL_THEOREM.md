# DMSV B1R2 structural theorem audit

Date: 2026-08-31
Graphiti: `0.29.3`
Theorem status: `STATIC_THEOREM_CONDITIONAL`

## Conditional statement

Let `W_t(f)` be the prompt-visible previous-episode projection used to build
the Node request for future source `f`, and let predecessor `p` be absent from
state `S_t` but durably published in `S_(t+1)`. If E1--E13 all hold, `p` is
visible to the Graphiti retrieval query, `last_n > 0`, no tie or later-source
leak exists, and the old/new prompt projections differ, then:

`W_(t+1)(f) != W_t(f)`.

Because Graphiti's Node template serializes `previous_episodes` into the user
message, a changed projection yields different canonical request bytes and
therefore a different request identity, provided the request binding and all
epochs are complete and equal.

This is a conditional theorem, not an exhaustive result over all transitions.
The current development evidence contains no pair satisfying every E1--E13,
so L4 `STRUCTURALLY_ALWAYS_DIRTY` is not established.

## Window cases

| Case | Expected effect if all E conditions pass | Current status |
|---|---|---|
| Window not full | predecessor appends to the visible chronological list | Conditional only |
| Window full | predecessor enters the last-`n` set and displaces the oldest visible episode | Conditional only |
| Selector miss | predecessor is filtered by group/source | `UNKNOWN` unless selector fields are bound |
| Reference-time miss | predecessor fails `valid_at <= reference_time` | `UNKNOWN` unless reference time is bound |
| `last_n=0` | no previous episode is visible | Explicit counterexample; excluded by E7 |
| Valid-time tie | ordering may be nondeterministic without stable secondary key | Explicit counterexample; excluded by E13 |
| Same projection | UUID/state membership can change while prompt bytes remain equal | Explicit counterexample |
| Node call omitted | no Node request comparison exists | Explicit counterexample; excluded by E9 |
| Epoch/template change | request can change for a non-state reason | Excluded by E10 |
| V6 context removal | extraction root may differ before Node resolution | Scoped by semantic-root audit |

## Proof obligations not discharged

The historical observer records order and projection digests, but not all
retrieval bindings, independent request binding, epochs or decoding contract.
The pair matrix therefore marks these obligations `UNKNOWN`. A single dirty
pair would establish only L2; even a development dirty rate would establish at
most L3. L4 requires exhaustive coverage of the frozen eligible domain, and L5
additionally requires an independent native-localization audit.

No batch split, prompt rewrite, previous-context deletion or provider call is
part of this theorem audit.
