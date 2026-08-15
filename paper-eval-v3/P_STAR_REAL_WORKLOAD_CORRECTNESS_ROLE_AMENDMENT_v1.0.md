# P* Real-Workload Correctness Role Amendment v1.0

Date: 2026-08-15

Status: frozen additive clarification. This document does not rewrite the
sealed S4 gate, the sealed real-workload correctness contract, any prior run,
or any result. It grants no model, Neo4j, live-execution, result-inspection, or
current-pointer authority.

Controlling inputs:

- `../（主实验）MemBind_PAPER_EVALUATION_WORKPLAN_v3_SMALL_FIRST_FINAL.md`
- `artifacts/paper_eval/native/S4_REVISED_OFFLINE_GATE.json`
- `artifacts/paper_eval/native/S5_METHOD_QUALIFICATION_PLAN.json`
- `runtime/CURRENT_STAGE_STATUS.json`

## 1. Purpose

The parent protocol deliberately includes P* as a naive whole-update parallel
baseline. P* is expected to expose the performance/correctness tradeoff of
coarse-grained concurrency; it is not a semantics-preserving method.

The sealed real-workload contract applied one hard-zero invariant merge rule
to U0, A0, P*, and M*. That rule is correct for methods whose claims require
Native-compatible behavior, but it would incorrectly erase the P* performance
record when P* itself causes an ordering or semantic violation. This amendment
resolves only that method-role conflict.

## 2. Unchanged Hard-Zero Gates

U0, A0, and M* retain the existing hard-zero merge gates:

```text
episode/source coverage                    = 100%
lost episode/source count                  = 0
duplicate episode/source count             = 0
source publication-order violations        = 0
visibility/publication violations          = 0
temporal/provenance violations             = 0
```

A violation makes that method result non-mergeable under the existing
contract. This amendment does not weaken those rules.

## 3. P* Scientific Role

P* remains the naive whole-update parallel performance/correctness tradeoff
baseline. Every scheduled source must receive a terminal classification, and
input accounting and telemetry coverage must both be 100%.

P* must persist and disclose at least:

```text
scheduled, published, failed, lost, and duplicate source counts
source-order, visibility/publication, and temporal/provenance violations
semantic graph-difference metrics
transaction and method failures
work volume, retries, event/checkpoint integrity, and drain/censoring status
```

When complete evidence shows a treatment-induced ordering, transaction, or
semantic violation, that observation is a scientific outcome of P*. It must
remain attached to the corresponding performance record and appear in later
reporting. It may not be silently deleted or relabeled as infrastructure
failure merely because it violates a Native invariant.

This retention rule does not validate P*. P* cannot claim semantics
preservation, correctness equivalence, quality equivalence, or quality
non-inferiority. Any P* quality measurement is descriptive and must be shown
with the full violation disclosure.

## 4. Evidence Failures

The distinction between a treatment result and an unusable run remains
strict:

```text
complete evidence + treatment-induced violation
  -> retain as a scientific P* outcome

incomplete accounting or telemetry
corrupt/unverifiable artifact
missing terminal source classifications
  -> infrastructure failure; non-mergeable
```

Thus this amendment does not relax accounting, artifact integrity, or
observability requirements.

## 5. Relationship To Revised S4

The revised S4 evidence lanes remain separate:

```text
real Native execution
  -> measured end-to-end performance, quality, and direct invariants

workload-level trace replay
  -> controlled scheduling analysis only; no semantic-correctness claim

bounded deterministic fixture
  -> exact mechanism/publication parity under controlled providers
```

Full cross-run correlation of Graphiti's internal candidate-resolution calls
is not reintroduced by this amendment. Sealed S4 and real-workload artifacts
remain unchanged; only the P* interpretation clause above supersedes the two
conflicting P* merge classifications.

## 6. Authority And Next Action

Current stage remains `S3_CONFIGURATION_FROZEN`. The only permitted next
action is S5 adapter implementation and offline tests under the already sealed
S5 plan. This amendment does not authorize model calls, Neo4j reads or writes,
live S5 execution, PILOT, formal evaluation, result generation or inspection,
namespace work, cleanup, or a current-stage pointer update.
