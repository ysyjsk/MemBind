# Required Counterexamples

1. A Read at snapshot 1 followed by a Read at snapshot 2 is a T1 violation.
2. A node name mutation whose embedding field is omitted from Delta makes
   `Complete_node_cosine=UNKNOWN`, not STABLE.
3. A short top-k result has no kth cutoff; an inserted candidate may enter it.
   A full top-k result has the same phantom risk when the inserted key was not
   in the old witness domain.
4. A boundary score tie is UNKNOWN because Graphiti has no consumer-visible
   secondary UUID order.
5. A previous-episode window change reaches extraction, resolution and
   attribute/summary demand through a typed data edge.
6. Two identical stable names are AMBIGUOUS; completion position cannot repair
   alignment.
7. A repaired node whose canonical output is equal reconverges. A dirty node
   with no repair result is UNKNOWN and propagates; treating missing repair as
   the old output is false reconvergence. Set-only comparison can also be
   falsely unaffected when prompt order changes.
8. A native continuation that consumes an endpoint UUID defeats plain alpha
   equivalence; the UUID must be included in seam K.
9. Provider agreement over repeated requests cannot create a ReplayAllowed
   contract when hidden session history is possible.
10. A bulk write followed by a crash before saga completion violates M2 atomic
    visibility if the frontier has already advanced.
11. An unchanged object delta with a changed query/index/embedder/config epoch
    is UNKNOWN; epoch presence alone cannot prove old/new epoch equality.
