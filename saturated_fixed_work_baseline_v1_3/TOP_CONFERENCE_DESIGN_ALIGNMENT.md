# Design alignment

MemBind's resource-credit scheduler follows the transferable ideas in Orca,
Sarathi-Serve, DistServe, Llumnix, and USHER: admission is a runtime scheduling
decision over dependency-ready work, phase/topology boundaries are explicit,
and capacity is derived from the serving envelope rather than selected from
the result of one workload. The implementation keeps one ordered authoritative
frontier and uses a conservative reservation before admitting speculative work.

It does not copy selective batching, chunked prefill, migration, SLO
optimisation, prediction, RL, bandits, or latency EWMA because those mechanisms
are outside the measured MemBind contract and would add unverified knobs. The
provider-free credit oracle is deliberately small: it uses only certified pool
capacity, active physical calls, authoritative reserve, an output envelope, and
an optional authoritative token budget. A/B/C share the same adapter and
serving resources; only ordering and admission treatment differ.
