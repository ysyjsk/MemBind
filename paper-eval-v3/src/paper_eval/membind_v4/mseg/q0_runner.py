"""One bounded V4-MSEG-Q0 diagnostic measurement runner."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from paper_eval.artifacts import append_jsonl_durable, atomic_write_json, payload_sha256, sha256_file
from paper_eval.membind_v31.certification import StateCutCertification
from paper_eval.membind_v31.live_block import V31LiveHooks
from paper_eval.membind_v31.optimization_live import execute_w4_pilot
from paper_eval.membind_v31.optimization_pilot import (
    BIND_WORKERS,
    COMPILE_WORKERS,
    GLOBAL_LLM_ADMISSION_K,
    LOOKAHEAD,
    PILOT_HISTORY,
    build_w4_pilot_contract,
    derive_w4_pilot_cache_salt,
    derive_w4_pilot_namespace,
)

from .qualification import Q0LiveComposition
from .q0_reducer import reduce_q0_qualification


class Q0RunnerError(ValueError):
    """The one-shot Q0 measurement failed or violated its diagnostic contract."""


def _fail(code: str) -> Q0RunnerError:
    return Q0RunnerError(code)


def _read_json(path: Path, code: str) -> dict[str, object]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None
    if not isinstance(value, dict):
        raise _fail(code)
    return value


def _read_pilot_rows(path: Path, code: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        raise _fail(code) from None
    for line in lines:
        try:
            wrapper = json.loads(line)
            record = wrapper["record"]
            row = record["row"]
        except (json.JSONDecodeError, KeyError, TypeError):
            raise _fail(code) from None
        if not isinstance(record, dict) or not isinstance(row, dict):
            raise _fail(code)
        if wrapper.get("record_sha256") != payload_sha256(record):
            raise _fail(f"{code}_hash_mismatch")
        rows.append(dict(row))
    return rows


def write_operator_trace(path: Path, events: Sequence[Mapping[str, object]]) -> None:
    """Persist content-safe operator events after execution, off the critical path."""

    target = Path(path)
    if target.exists():
        raise _fail("operator_trace_already_exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not events:
        # A failed run can legitimately have no adapter span.  Still leave a
        # durable, hashable trace artifact so the failure boundary is explicit.
        descriptor = os.open(
            target,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o664,
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        directory = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return
    for event in events:
        if not isinstance(event, Mapping):
            raise _fail("operator_event_invalid")
        record = {
            "schema_version": "membind.paper-eval-v4.mseg-q0-operator.v1",
            "row": deepcopy(dict(event)),
        }
        append_jsonl_durable(
            target,
            {"record": record, "record_sha256": payload_sha256(record)},
        )


def _read_operator_rows(path: Path) -> list[dict[str, object]]:
    return _read_pilot_rows(path, "operator_trace_unreadable")


def implementation_identity(project_root: Path) -> str:
    relative = (
        "src/paper_eval/membind_v31/request_runtime.py",
        "src/paper_eval/membind_v31/live_runtime.py",
        "src/paper_eval/membind_v31/coordinator.py",
        "src/paper_eval/membind_v31/graphiti_adapter.py",
        "src/paper_eval/membind_v4/mseg/observability.py",
        "src/paper_eval/membind_v4/mseg/instrumented_adapter.py",
        "src/paper_eval/membind_v4/mseg/qualification.py",
        "src/paper_eval/membind_v4/mseg/q0_reducer.py",
    )
    files = {name: Path(project_root, name) for name in relative}
    if any(not path.is_file() for path in files.values()):
        raise _fail("implementation_file_missing")
    return payload_sha256({name: sha256_file(path) for name, path in files.items()})


# Kept for callers from the initial Q0 draft; new code should use the public
# helper above so the identity contract is explicit at the CLI boundary.
_implementation_identity = implementation_identity


def render_q0_decision(
    *,
    reduced: Mapping[str, object],
    output_root: Path,
    baseline_root: Path,
) -> str:
    status = str(reduced.get("status"))
    passed = status == "PASS_INSTRUMENTATION_QUALIFICATION"
    reasons = reduced.get("blocking_reasons")
    reason_text = ", ".join(str(item) for item in reasons) if isinstance(reasons, list) else ""
    return f"""# V4-MSEG-Q0 Fine-Grained Causal Telemetry Qualification

```text
STATUS: {status}
MEASUREMENT_ONLY: yes
NEW_MECHANISM_AUTHORIZED: no
NEW_SCHEDULER_AUTHORIZED: no
BASELINE_SEALED: yes
BASELINE_ROOT: {baseline_root}
Q0_ROOT: {output_root}
POST_Q0_ACTION: {reduced.get('post_q0_action')}
BLOCKING_REASONS: {reason_text or 'none'}
```

Q0 changes observability only. It does not change Graphiti prompts, schemas,
model/backend, arrival offsets, compile workers, lookahead, bind workers,
request admission K, scheduler, dependency policy, or persistence semantics.

`client running`, vLLM batch membership, and GPU execution remain distinct;
the Q0 trace makes no backend-batch claim. Read scope remains
`NOT_OBSERVABLE` unless Graphiti directly exposes exact candidate IDs. Final
resolved UUIDs are effect evidence, not a read set.

The sealed W=4 pilot remains immutable. A PASS authorizes only offline MSEG
reconstruction and O1/O2/O3/O4 replay; it does not authorize a mechanism live
run. A FAIL stops fine-grained claims at this qualification boundary.
"""


async def execute_q0_measurement(
    *,
    contract: Mapping[str, object],
    verified_formal_plan: Mapping[str, object],
    episodes: Sequence[object],
    env: Mapping[str, str],
    output_root: Path,
    state_cut_certification: StateCutCertification,
    implementation_sha256: str,
    composition: Q0LiveComposition,
    baseline_root: Path,
    hooks: V31LiveHooks | None = None,
) -> dict[str, object]:
    """Execute exactly one Q0 pilot, then reduce against the sealed W=4 trace."""

    if hooks is not None:
        raise _fail("q0_hooks_reserved_use_composition")
    root = Path(output_root)
    baseline = Path(baseline_root)
    if not baseline.is_dir():
        raise _fail("baseline_root_missing")
    if not isinstance(composition, Q0LiveComposition):
        raise _fail("q0_composition_invalid")
    result: dict[str, object] | None = None
    try:
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
    finally:
        if root.is_dir():
            write_operator_trace(root / "V4_MSEG_Q0_OPERATOR_TRACE.jsonl", composition.observer.events)

    if result is None:
        raise _fail("q0_execution_missing_result")
    q0_manifest = _read_json(root / "manifest.json", "q0_manifest_unreadable")
    q0_rows = _read_pilot_rows(root / "llm.jsonl", "q0_llm_unreadable")
    operator_rows = _read_operator_rows(root / "V4_MSEG_Q0_OPERATOR_TRACE.jsonl")
    baseline_manifest = _read_json(baseline / "manifest.json", "baseline_manifest_unreadable")
    baseline_result = _read_json(baseline / "result.json", "baseline_result_unreadable")
    baseline_rows = _read_pilot_rows(baseline / "llm.jsonl", "baseline_llm_unreadable")
    baseline_state = composition.comparison_state
    q0_snapshots = composition.q0_namespace_snapshots or []
    q0_state = q0_snapshots[-1] if q0_snapshots else None
    if not isinstance(baseline_state, dict) or not isinstance(q0_state, dict):
        raise _fail("q0_published_state_unavailable")

    reduced = reduce_q0_qualification(
        baseline_result=baseline_result,
        q0_result=result,
        baseline_manifest=baseline_manifest,
        q0_manifest=q0_manifest,
        baseline_request_rows=baseline_rows,
        q0_request_rows=q0_rows,
        operator_events=operator_rows,
        baseline_state=baseline_state,
        q0_state=q0_state,
    )
    atomic_write_json(root / "V4_MSEG_Q0_REDUCED.json", reduced)
    body: dict[str, object] = {
        "schema_version": "membind.paper-eval-v4.mseg-q0-result.v1",
        "status": reduced["status"],
        "measurement_only": True,
        "formal_main_table_eligible": False,
        "new_mechanism_authorized": False,
        "new_scheduler_authorized": False,
        "history_id": PILOT_HISTORY,
        "source_sequences": list(range(12)),
        "q0_pilot_result_sha256": payload_sha256(result),
        "baseline_manifest_sha256": sha256_file(baseline / "manifest.json"),
        "baseline_result_sha256": sha256_file(baseline / "result.json"),
        "baseline_llm_trace_sha256": sha256_file(baseline / "llm.jsonl"),
        "q0_llm_trace_sha256": sha256_file(root / "llm.jsonl"),
        "q0_operator_trace_sha256": sha256_file(
            root / "V4_MSEG_Q0_OPERATOR_TRACE.jsonl"
        ),
        "reduced": reduced,
    }
    sealed = {**body, "payload_sha256": payload_sha256(body)}
    atomic_write_json(root / "V4_MSEG_Q0_RESULT.json", sealed)
    (root / "V4_MSEG_Q0_DECISION.md").write_text(
        render_q0_decision(reduced=reduced, output_root=root, baseline_root=baseline),
        encoding="utf-8",
    )
    return sealed


__all__ = [
    "Q0RunnerError",
    "execute_q0_measurement",
    "implementation_identity",
    "render_q0_decision",
    "write_operator_trace",
]
