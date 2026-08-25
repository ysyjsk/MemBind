# Unsupported and Unknown Operators

The following are not silently treated as stable:

* BM25 because corpus statistics, analyzer, index epoch and tie behavior are
  not part of a declared proof contract.
* Hybrid/RRF because every channel and fused consumer order must be stable.
* ANN because backend recall/order guarantees are not sealed.
* A closed persistent Apply for the Graphiti tail because missing embeddings,
  bulk transaction callbacks and optional saga/community work can read or
  write after the proposed seam.
* Live response replay without a provider/deployment semantic contract that
  excludes hidden session/history/tool/server state and external side effects.

UNKNOWN is a legal status. It causes a fresh dependent region or response and
is included in R1/R3 confusion matrices; it is never counted as INVALID or
CHANGED merely to improve a hit rate.
