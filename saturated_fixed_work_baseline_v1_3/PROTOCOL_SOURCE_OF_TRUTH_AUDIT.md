# Protocol Source-of-Truth Audit

The active source of truth is the v1.3 package under
`saturated_fixed_work_baseline_v1_3/` plus the explicitly reused v1.2 runner
primitives. The following boundaries are now explicit:

| Concern | Active source | Historical/reference only |
| --- | --- | --- |
| workload and source order | v1.2 dataset loaded by `simple_campaign.py` | sealed v1.2 records |
| B0/B1 execution | v1.2 schedule and live block | old reports |
| common serving behavior | `configs/frozen_backend_v1_3.json` and `frozen_client_v1_3.json` | prior launch notes |
| block timing | `block_lifecycle.py` and the v1.2 lifecycle reducer | legacy block reports |
| semantic fingerprint | `membind_v5/semantic_fingerprint.py` plus `real_seam_observer.py` | prior offline-only qualification |
| old v3.1 seam | no active import in v1.3 cleanup path | `paper-eval-v3/src/paper_eval/membind_v31/` |

The active protocol no longer treats physical process identity or sampling
provenance as validity requirements. The endpoint values explicitly present in
the frozen launch commands are retained; omitted serving defaults remain
provider-version defaults. B0 and B1 resolve to the same client dictionary and
the contract rejects method-specific overrides.

No V5 runtime, scheduler, admission policy, held-out history, or live
source-0 diagnostic was introduced by this audit.
