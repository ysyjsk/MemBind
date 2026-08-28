# MemBind V6.1

V6.1 is an isolated performance-engineering branch of V6 exact
native-demand extraction replay. It is not V7, does not broaden V6 replay
eligibility, and does not modify the frozen V6 implementation.

Current status: `LOCAL_LIVE_AUTORESEARCH_ACTIVE`.

- The original offline design is in `V6_1_OFFLINE_OPTIMIZATION_DESIGN.md`.
- The immutable parent boundary is in `VERSION_BOUNDARY.json`.
- The 2026-08-27 local campaign is authorized only under profile
  `local-qwen3-14b-awq-v1`; its controlling plan is
  `../../MemBind_V6_1_Local_Qwen_Autoresearch_Workplan.md`.
- Runtime implementation lives under the new Python namespace
  `saturated_fixed_work_baseline_v1_3.membind_v6_1`, uses the separate
  `scripts/run_mab_v61_local.py` CLI and writes below `/data/predator/ly/Mem`.
- `membind_v6`, `mab_live_runner.py`, V5 runtime files, frozen 32B configs, and
  sealed 32B artifacts remain unchanged and read-only.
- Provider calls and graph writes are authorized only after activating
  `scripts/local_runtime/activate.sh`, verifying the local READY marker and
  both localhost model catalogs, and creating a fresh profile-qualified
  namespace.

V6.1-Core may optimize evidence integrity, admission, lookahead, foreground
protection, speculation selection, and adaptive backpressure without changing
Graphiti semantics. Suffix transformations such as timestamp batching,
dedupe-result caching, or embedding reuse belong to a separately gated
V6.1-Suffix lane and cannot enter the first live candidate without an exact
offline refinement proof.

The older document's `OFFLINE_DESIGN_ONLY` terminal wording is superseded for
this local profile campaign. Its semantic constraints and diagnosis remain
applicable; its complex promotion gates are advisory rather than blocking.
