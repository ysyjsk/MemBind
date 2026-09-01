# Structured Output Qualification

Status: `PASS_ACTUAL_RUNTIME_CALLSITE`; synthetic suite: `PASS_PROVIDER_FREE_SYNTHETIC_CALLSITE_SUITE`.

Certified `17` callsites across `47` generated variants using the local Qwen tokenizer, a `65536` token context limit, the pinned `extract_edges.edge` `16384` token completion budget, the `32768` token default budget for other captured callsites, and a `32` token safety margin. Caller-supplied attribute schemas and candidate-flight capacities are bounded before provider invocation.

The edge certificate's worst-case compact JSON is `15862` tokens with a `1900`-character fact cap, leaving `522` tokens below the pinned edge budget. Timestamp batches are capped at `63` items and certify at `32272` tokens. Certified truncation and context-budget failures have zero automatic resend variants; only transient transport failures receive at most two extra physical attempts under the shared identity contract. Model revision: `31c69efc29464b6bb0aee1398b5a7b50a99340c3`.

R3 is `AT_LEAST_ONCE_WITH_STABLE_IDEMPOTENCY_KEY`; no cross-system durable reconciliation or exactly-once claim is made.

Source-discovered callsites: `15`; actual observed: `16`; covered names: `11`; uncovered names: `0`; covered source rows: `14`; unreachable with proof: `1`.
Evaluated source bundle SHA-256: `c80f1399763e9849cd82f7fb8e1861c2b8c4359a702a4c5795dc3db38be8ecd8`; generator source SHA-256: `06dbd73b160e4f5118b71ed2675ac39411dfaae1694d664ba870f2ed96ed6241`; base code commit: `58af2320c5a606195968dbfd5704eaf2a805c8fd`.
