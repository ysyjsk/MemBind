# DVSR Phase 2 TDD Report

Status: **PHASE_2_PROVIDER_FREE_TDD_SEALED**

The first certificate implementation was intentionally tested before it was
repaired.  The adversarial suite produced nine RED cases: model/read epoch
changes, filter/group/K/threshold changes, prompt payload changes, batch
membership/order changes, NaN scores, mixed snapshots, incomplete deltas,
candidate deletion, and a cutoff boundary without an explicit total-order
contract.  The repair was moved to a new `dvsr_certificates.py` identity so the
legacy V7 `certificates.py` hash freeze remained intact.

The GREEN suite now has zero false `VALID` outcomes for those cases.  A score
sidecar is accepted only for the narrow cosine `name_embedding` candidate
contract already covered by the legacy theory tests.  All environment changes,
prompt-visible changes, batching changes, incomplete images, non-finite values,
and unknown tie/order contracts fail closed to `UNKNOWN`.

The observer schema requires the complete read epoch/read-set, canonical
request, actual touched-write delta, no-write proof, continuation digest, and
critical-path work fields before a record can be `VALID`.  `CUT-D` is a nested
prefix and its comparator has no C1 theorem: any upstream/order/batch/schema or
request change is `UNKNOWN`, so a later implementation must use C0 fresh or C2
repair.

No provider call, database write, speculative publication, held-out history, or
live treatment was used in this phase.  The legal next step is a read-only
observer on the four exposed development histories after the Frozen-V6 seam is
bound to real capture fields.
