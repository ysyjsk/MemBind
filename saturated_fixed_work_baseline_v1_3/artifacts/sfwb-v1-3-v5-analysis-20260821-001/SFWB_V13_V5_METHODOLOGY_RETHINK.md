# SFWB v1.3 V5 methodology rethink

## Work conservation

The fixed-work contract makes realized semantic work a first-class outcome. Native serial is 255 calls / 717,681 input tokens / 572 embedding items; MemBind is 316 / 729,048 / 747; B1 is 184 / 213,288 / 448. The near-identical B1 and MemBind makespans therefore do not establish equivalent execution efficiency. The primary candidate is semantic work conservation: preserve the serial logical call plan and batching while changing overlap.

## Semantic equivalence

`Update_i = EvidenceWork_i + StateWork_i` remains a useful decomposition, but the stronger contract is serial-equivalent state-derived input and deterministic effect/publication behavior. Direct rule violations being zero is necessary, not sufficient. The serial self-floor and policy graph diffs show that state cut/candidate visibility must be part of V5 correctness.

## Saturation and bottlenecks

Semantic legality, application admission, and backend capacity are separate variables. The sealed blocks report `resource_availability=NOT_EVALUATED`; this analysis therefore cannot claim backend saturation or authorize a K sweep. Service-time variance is measured, but causal attribution to a capacity envelope is not.

## EvidenceWork versus StateWork

Extraction is evidence-derived and can overlap; candidate search, resolution, effect, persistence, and publication are state-derived and require the correct state cut. MEG remains valuable as a semantic representation for this distinction, logical work identity, publication correctness, and work-conservation validation. It is not promoted to a default headline scheduler mechanism.

## Candidate methodology (not implemented)

`State-Cut + Semantic Work Conservation + Backend-Saturated Legal Execution` is a research candidate only. No scheduler, dynamic admission, stale-state read, or new instrumentation was implemented in this round.
