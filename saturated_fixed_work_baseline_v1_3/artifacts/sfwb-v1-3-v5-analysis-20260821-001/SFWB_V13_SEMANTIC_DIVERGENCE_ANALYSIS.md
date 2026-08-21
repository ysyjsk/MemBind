# SFWB v1.3 semantic divergence analysis

## Serial self-divergence floor

B0-A versus B0-B is the empirical serial floor, not zero: `{"attribute": 6, "edge_key": 4, "entity_key": 2, "source_link": 4, "temporal": 6}` normalized graph differences and `51` input-token delta with zero logical-call delta. The qualification flag `canonical_exact_match=true` is not used as a cross-attempt equality claim.

## Policy comparisons

| comparison | exact | entity | edge | attribute | temporal | source link | call delta | input-token delta |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| B0-A_vs_B0-B | False | 2 | 4 | 6 | 6 | 4 | 0 | 51 |
| B0-A_vs_B1 | False | 50 | 181 | 133 | 183 | 181 | -71 | -504393 |
| B0-A_vs_MemBind-v3.1 | False | 47 | 242 | 129 | 242 | 242 | 61 | 11367 |

All four blocks report zero direct semantic violations; MemBind reports complete 12/12 publication coverage. That protocol/direct-safety result is distinct from semantic outcome equivalence: B1 and MemBind both differ materially from the serial graph, and MemBind's difference exceeds the serial floor across every reported graph category.
