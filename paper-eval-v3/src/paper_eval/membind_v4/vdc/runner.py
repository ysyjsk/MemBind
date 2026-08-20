"""Single bounded live capture runner for the MemBind-VDC oracle."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from paper_eval.artifacts import atomic_write_json, payload_sha256, sha256_file
from paper_eval.membind_v31.certification import StateCutCertification
from paper_eval.membind_v31.live_block import V31LiveHooks
from paper_eval.membind_v31.optimization_live import execute_w4_pilot

from .artifacts import (
    build_vdc_oracle_rows,
    read_publication_times,
    write_vdc_bundle,
)
from .live_composition import VDCCaptureComposition
from .oracle import reduce_vdc_oracle


class VDCRunnerError(ValueError):
    """The bounded VDC capture run failed or could not produce an oracle."""


def _fail(code: str) -> VDCRunnerError:
    return VDCRunnerError(code)


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise _fail(code)
    return value


def implementation_identity(project_root: Path) -> str:
    relative = (
        "src/paper_eval/membind_v4/vdc/capture.py",
        "src/paper_eval/membind_v4/vdc/certificate.py",
        "src/paper_eval/membind_v4/vdc/replay.py",
        "src/paper_eval/membind_v4/vdc/observation_adapter.py",
        "src/paper_eval/membind_v4/vdc/live_composition.py",
        "src/paper_eval/membind_v4/vdc/artifacts.py",
        "src/paper_eval/membind_v4/vdc/runner.py",
    )
    files = {name: Path(project_root, name) for name in relative}
    if any(not path.is_file() for path in files.values()):
        raise _fail("implementation_file_missing")
    return payload_sha256({name: sha256_file(path) for name, path in files.items()})


def render_vdc_decision(
    *,
    oracle: Mapping[str, object],
    output_root: Path,
    run_id: str,
) -> str:
    decision = oracle.get("decision")
    if not isinstance(decision, Mapping):
        raise _fail("oracle_decision_invalid")
    counts = oracle.get("counts")
    if not isinstance(counts, Mapping):
        raise _fail("oracle_counts_invalid")
    return f"""# MemBind-VDC Certificate Oracle Decision

```text
RUN_ID: {run_id}
STATUS: {decision.get('status')}
REASON: {decision.get('reason')}
LIVE_CANDIDATE_AUTHORIZED: {decision.get('live_candidate_authorized')}
OUTPUT_ROOT: {output_root}
```

This artifact is a capture-only measurement. It does not change the frozen
v3.1 arrival trace, workload, model, backend, W=4, K=2, publication order, or
main-table results. A certificate predicts profitability only; exact
predecessor-state validation remains the correctness gate.

```text
future_prepared_before_publication = {counts.get('future_prepared_before_publication_count')}
stale_probe_ready_before_publication = {counts.get('stale_probe_ready_before_publication_count')}
certified_disjoint = {counts.get('certified_disjoint_count')}
certified_conflict = {counts.get('certified_conflict_count')}
unknown = {counts.get('unknown_count')}
exact_validation = {counts.get('exact_validation_count')}
validation_hit = {counts.get('validation_hit_count')}
validation_miss = {counts.get('validation_miss_count')}
hideable_node_resolve_service_ns = {oracle.get('total_hideable_node_resolve_service_ns')}
```
"""


async def execute_vdc_capture(
    *,
    contract: Mapping[str, object],
    verified_formal_plan: Mapping[str, object],
    episodes: Sequence[object],
    env: Mapping[str, str],
    output_root: Path,
    state_cut_certification: StateCutCertification,
    implementation_sha256: str,
    composition: VDCCaptureComposition,
) -> dict[str, object]:
    """Run one fixed 12-source factorized capture, then reduce offline."""

    if not isinstance(composition, VDCCaptureComposition):
        raise _fail("capture_composition_invalid")
    if not isinstance(state_cut_certification, StateCutCertification):
        raise _fail("state_cut_certification_invalid")
    _sha(implementation_sha256, "implementation_sha256_invalid")
    root = Path(output_root)
    if root.exists():
        raise _fail("capture_output_root_not_fresh")
    result = await execute_w4_pilot(
        contract=contract,
        verified_formal_plan=verified_formal_plan,
        episodes=episodes,
        env=env,
        output_root=root,
        state_cut_certification=state_cut_certification,
        implementation_sha256=implementation_sha256,
        hooks=composition.hooks,
    )
    bundle_path = root / "VDC_CAPTURE_BUNDLE.json"
    bundle_document = write_vdc_bundle(bundle_path, composition.bundle)
    try:
        publication_times = read_publication_times(root / "events.jsonl")
        rows = build_vdc_oracle_rows(
            composition.bundle,
            publication_times=publication_times,
            expected_source_sequences=tuple(range(1, 12)),
        )
        oracle = reduce_vdc_oracle(rows, expected_source_sequences=tuple(range(1, 12)))
    except Exception as error:
        raise _fail(f"vdc_oracle_reduction_failed:{error}") from error
    oracle_path = root / "VDC_CERTIFICATE_ORACLE.json"
    atomic_write_json(oracle_path, oracle)
    decision_path = root / "VDC_DECISION.md"
    decision_path.write_text(
        render_vdc_decision(
            oracle=oracle,
            output_root=root,
            run_id=str(contract.get("pilot_run_id", "unknown")),
        ),
        encoding="utf-8",
    )
    body: dict[str, object] = {
        "schema_version": "membind.paper-eval-v4.vdc-capture-result.v1",
        "status": "VDC_CAPTURE_COMPLETED",
        "capture_only": True,
        "formal_main_table_eligible": False,
        "new_mechanism_authorized": False,
        "new_scheduler_authorized": False,
        "history_id": contract.get("history_id"),
        "source_sequences": list(range(12)),
        "pilot_result_sha256": payload_sha256(result),
        "capture_bundle_sha256": sha256_file(bundle_path),
        "oracle_sha256": sha256_file(oracle_path),
        "bundle_counts": {
            "captures": len(composition.bundle.captures),
            "prepared": len(composition.bundle.prepared),
            "stale_probes": len(composition.bundle.stale_probes),
            "exact_reads": len(composition.bundle.exact_reads),
        },
        "oracle": oracle,
        "network_calls_in_reducer": False,
        "persistent_writes_in_reducer": False,
    }
    atomic_write_json(root / "VDC_CAPTURE_RESULT.json", {**body, "payload_sha256": payload_sha256(body)})
    return {**body, "payload_sha256": payload_sha256(body)}


__all__ = [
    "VDCRunnerError",
    "execute_vdc_capture",
    "implementation_identity",
    "render_vdc_decision",
]

