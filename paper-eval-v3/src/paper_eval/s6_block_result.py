"""Pure builder and finalized verifier for one complete S6 calibration block."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path

from . import PROTOCOL_VERSION
from .artifacts import payload_sha256
from .s6_calibration_contract import (
    compute_s6_block_metrics,
    verify_s6_cell_identity,
)


SCHEMA = "membind.paper-eval-v3.s6-block-result.v1"
STAGE = "S6_DEVELOPMENT_ONLY_CONCURRENCY_CALIBRATION"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_MATRIX_FIELDS = {"file_sha256", "payload_sha256", "matrix_sha256"}
_WORKLOAD_FIELDS = {"source_count", "source_manifest_sha256"}
_RUNNER_FIELDS = {"status", "evidence_payload_sha256", "events_file_sha256"}
_CORRECTNESS_FIELDS = {
    "direct_hard_violation_count",
    "deterministic_correctness_gate",
    "hidden_fallback_count",
}
_WORK_VOLUME_FIELDS = {
    "llm_call_count",
    "llm_prompt_tokens",
    "llm_completion_tokens",
    "embedding_call_count",
    "embedding_input_count",
    "db_query_count",
    "db_transaction_count",
    "db_write_count",
}
_REQUIRED_WORK_VOLUME = {
    "llm_call_count",
    "llm_prompt_tokens",
    "llm_completion_tokens",
    "embedding_call_count",
    "embedding_input_count",
}
_BINDING_FIELDS = {
    "preflight_file_sha256",
    "preflight_payload_sha256",
    "authority_file_sha256",
    "authority_payload_sha256",
    "consumption_file_sha256",
    "consumption_payload_sha256",
    "post_observation_file_sha256",
    "post_observation_payload_sha256",
}
_PAYLOAD_FIELDS = {
    "schema_version",
    "stage",
    "status",
    "cell",
    "matrix",
    "workload",
    "execution_identity_sha256",
    "runner",
    "source_outcomes",
    "metrics",
    "terminal_accounting",
    "correctness",
    "work_volume",
    "bindings",
    "selection_eligibility",
    "block_result_sha256",
}


class S6BlockResultError(ValueError):
    """A complete block result is malformed, inconsistent, or not terminal."""


def _fail(code: str) -> S6BlockResultError:
    return S6BlockResultError(code)


def _mapping(value: object, code: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise _fail(code)
    return deepcopy(dict(value))


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def _count(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _hash_mapping(value: object, fields: set[str], code: str) -> dict[str, str]:
    selected = _mapping(value, code)
    if set(selected) != fields:
        raise _fail(code)
    return {key: _sha(selected.get(key), f"{key}_invalid") for key in sorted(fields)}


def _workload(value: object) -> dict[str, object]:
    selected = _mapping(value, "workload_invalid")
    if set(selected) != _WORKLOAD_FIELDS:
        raise _fail("workload_invalid")
    count = _count(selected.get("source_count"), "workload_source_count_invalid")
    if count < 1:
        raise _fail("workload_source_count_invalid")
    return {
        "source_count": count,
        "source_manifest_sha256": _sha(
            selected.get("source_manifest_sha256"), "source_manifest_invalid"
        ),
    }


def _runner(value: object, method: str) -> dict[str, str]:
    selected = _mapping(value, "runner_invalid")
    if set(selected) != _RUNNER_FIELDS:
        raise _fail("runner_invalid")
    status = selected.get("status")
    allowed = (
        {"PASS", "SCIENTIFIC_OUTCOME_COMPLETE"}
        if method == "P*"
        else {"PASS"}
    )
    if status not in allowed:
        raise _fail("runner_status_invalid")
    return {
        "status": str(status),
        "evidence_payload_sha256": _sha(
            selected.get("evidence_payload_sha256"), "runner_evidence_invalid"
        ),
        "events_file_sha256": _sha(
            selected.get("events_file_sha256"), "runner_events_invalid"
        ),
    }


def _correctness(value: object) -> dict[str, object]:
    selected = _mapping(value, "correctness_invalid")
    if set(selected) != _CORRECTNESS_FIELDS:
        raise _fail("correctness_invalid")
    gate = selected.get("deterministic_correctness_gate")
    if gate not in {"PASS", "FAIL"}:
        raise _fail("correctness_gate_invalid")
    return {
        "direct_hard_violation_count": _count(
            selected.get("direct_hard_violation_count"), "violation_count_invalid"
        ),
        "deterministic_correctness_gate": str(gate),
        "hidden_fallback_count": _count(
            selected.get("hidden_fallback_count"), "fallback_count_invalid"
        ),
    }


def _work_volume(value: object) -> dict[str, int | None]:
    selected = _mapping(value, "work_volume_invalid")
    if set(selected) != _WORK_VOLUME_FIELDS:
        raise _fail("work_volume_invalid")
    result: dict[str, int | None] = {}
    for field in sorted(_WORK_VOLUME_FIELDS):
        item = selected.get(field)
        if item is None and field not in _REQUIRED_WORK_VOLUME:
            result[field] = None
        else:
            result[field] = _count(item, f"work_volume_{field}_invalid")
    return result


def verify_s6_work_volume(value: Mapping[str, object]) -> dict[str, int | None]:
    """Validate the shared controller-to-result work-volume projection."""

    return _work_volume(value)


def _selection(
    *, method: str, correctness: Mapping[str, object]
) -> dict[str, object]:
    if method == "P*":
        return {
            "pstar_performance_eligible": True,
            "mstar_qualified": None,
            "mstar_disqualification_reasons": [],
        }
    reasons: list[str] = []
    if correctness["direct_hard_violation_count"] != 0:
        reasons.append("DIRECT_HARD_VIOLATION")
    if correctness["deterministic_correctness_gate"] != "PASS":
        reasons.append("CORRECTNESS_GATE_NOT_PASS")
    if correctness["hidden_fallback_count"] != 0:
        reasons.append("HIDDEN_FALLBACK")
    return {
        "pstar_performance_eligible": False,
        "mstar_qualified": not reasons,
        "mstar_disqualification_reasons": reasons,
    }


def _outcomes(value: object) -> list[dict[str, object]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _fail("source_outcomes_invalid")
    return [
        deepcopy(dict(item)) if isinstance(item, Mapping) else {}
        for item in value
    ]


def _derived(
    *,
    method: str,
    runner: Mapping[str, str],
    workload: Mapping[str, object],
    source_outcomes: Sequence[Mapping[str, object]],
    correctness: Mapping[str, object],
) -> tuple[dict[str, object], dict[str, int], dict[str, object]]:
    try:
        metrics = compute_s6_block_metrics(
            expected_source_count=int(workload["source_count"]),
            source_outcomes=source_outcomes,
        )
    except Exception:
        raise _fail("source_outcomes_invalid") from None
    accounting = {
        "expected": int(metrics["expected_source_count"]),
        "published": int(metrics["published_source_count"]),
        "failed": int(metrics["failed_source_count"]),
        "censored": int(metrics["censored_source_count"]),
        "lost": 0,
        "duplicate": 0,
    }
    if accounting["published"] + accounting["failed"] + accounting["censored"] != accounting[
        "expected"
    ]:
        raise _fail("terminal_accounting_invalid")
    if runner["status"] == "PASS" and (
        accounting["published"] != accounting["expected"]
        or accounting["failed"] != 0
        or accounting["censored"] != 0
    ):
        raise _fail("runner_outcome_binding_invalid")
    if runner["status"] == "SCIENTIFIC_OUTCOME_COMPLETE" and accounting["failed"] < 1:
        raise _fail("runner_outcome_binding_invalid")
    return metrics, accounting, _selection(method=method, correctness=correctness)


def build_s6_block_result(
    *,
    cell: Mapping[str, object],
    matrix_binding: Mapping[str, object],
    workload: Mapping[str, object],
    execution_identity_sha256: str,
    runner: Mapping[str, object],
    source_outcomes: Sequence[Mapping[str, object]],
    correctness: Mapping[str, object],
    work_volume: Mapping[str, object],
    bindings: Mapping[str, object],
) -> dict[str, object]:
    """Build a self-recomputable complete block payload without live I/O."""

    try:
        selected_cell = verify_s6_cell_identity(cell)
    except Exception:
        raise _fail("cell_identity_invalid") from None
    matrix = _hash_mapping(
        matrix_binding, _MATRIX_FIELDS, "matrix_binding_invalid"
    )
    selected_workload = _workload(workload)
    selected_runner = _runner(runner, str(selected_cell["method"]))
    selected_outcomes = _outcomes(source_outcomes)
    selected_correctness = _correctness(correctness)
    metrics, accounting, selection = _derived(
        method=str(selected_cell["method"]),
        runner=selected_runner,
        workload=selected_workload,
        source_outcomes=selected_outcomes,
        correctness=selected_correctness,
    )
    body: dict[str, object] = {
        "schema_version": SCHEMA,
        "stage": STAGE,
        "status": "SCIENTIFIC_OUTCOME_COMPLETE",
        "cell": selected_cell,
        "matrix": matrix,
        "workload": selected_workload,
        "execution_identity_sha256": _sha(
            execution_identity_sha256, "execution_identity_invalid"
        ),
        "runner": selected_runner,
        "source_outcomes": selected_outcomes,
        "metrics": metrics,
        "terminal_accounting": accounting,
        "correctness": selected_correctness,
        "work_volume": _work_volume(work_volume),
        "bindings": _hash_mapping(bindings, _BINDING_FIELDS, "bindings_invalid"),
        "selection_eligibility": selection,
    }
    body["block_result_sha256"] = payload_sha256(body)
    return verify_s6_block_result_payload(body)


def verify_s6_block_result_payload(value: Mapping[str, object]) -> dict[str, object]:
    payload = _mapping(value, "block_result_payload_invalid")
    if set(payload) != _PAYLOAD_FIELDS:
        raise _fail("block_result_payload_shape_invalid")
    seal = payload.pop("block_result_sha256", None)
    if (
        payload.get("schema_version") != SCHEMA
        or payload.get("stage") != STAGE
        or payload.get("status") != "SCIENTIFIC_OUTCOME_COMPLETE"
        or seal != payload_sha256(payload)
    ):
        raise _fail("block_result_seal_invalid")
    try:
        cell = verify_s6_cell_identity(payload.get("cell", {}))
    except Exception:
        raise _fail("cell_identity_invalid") from None
    matrix = _hash_mapping(payload.get("matrix"), _MATRIX_FIELDS, "matrix_binding_invalid")
    workload = _workload(payload.get("workload"))
    runner = _runner(payload.get("runner"), str(cell["method"]))
    source_outcomes = _outcomes(payload.get("source_outcomes"))
    correctness = _correctness(payload.get("correctness"))
    metrics, accounting, selection = _derived(
        method=str(cell["method"]),
        runner=runner,
        workload=workload,
        source_outcomes=source_outcomes,
        correctness=correctness,
    )
    if (
        payload.get("metrics") != metrics
        or payload.get("terminal_accounting") != accounting
        or payload.get("selection_eligibility") != selection
    ):
        raise _fail("block_result_derived_fields_invalid")
    payload.update(
        cell=cell,
        matrix=matrix,
        workload=workload,
        execution_identity_sha256=_sha(
            payload.get("execution_identity_sha256"), "execution_identity_invalid"
        ),
        runner=runner,
        source_outcomes=source_outcomes,
        correctness=correctness,
        work_volume=_work_volume(payload.get("work_volume")),
        bindings=_hash_mapping(
            payload.get("bindings"), _BINDING_FIELDS, "bindings_invalid"
        ),
    )
    payload["block_result_sha256"] = seal
    return payload


def verify_s6_block_result(value: Mapping[str, object]) -> dict[str, object]:
    artifact = _mapping(value, "block_result_invalid")
    if set(artifact) != {
        "protocol_version",
        "git_commit",
        "run_id",
        "status",
        "payload",
        "payload_sha256",
    }:
        raise _fail("block_result_envelope_shape_invalid")
    payload = verify_s6_block_result_payload(
        _mapping(artifact.get("payload"), "block_result_payload_invalid")
    )
    if (
        artifact.get("protocol_version") != PROTOCOL_VERSION
        or _GIT_COMMIT.fullmatch(str(artifact.get("git_commit", ""))) is None
        or artifact.get("run_id") != f"{payload['cell']['run_id']}-block-result"
        or artifact.get("status") != "finalized"
        or artifact.get("payload_sha256") != payload_sha256(payload)
    ):
        raise _fail("block_result_envelope_invalid")
    artifact["payload"] = payload
    return artifact


def _write_exclusive(path: Path, value: Mapping[str, object]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("ascii")
    descriptor = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
    try:
        offset = 0
        while offset < len(encoded):
            written = os.write(descriptor, encoded[offset:])
            if written == 0:
                raise OSError("short write while sealing S6 block result")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(output.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def finalize_s6_block_result(
    *, output_path: Path, payload: Mapping[str, object], git_commit: str
) -> dict[str, object]:
    selected = verify_s6_block_result_payload(payload)
    artifact = verify_s6_block_result(
        {
            "protocol_version": PROTOCOL_VERSION,
            "git_commit": str(git_commit),
            "run_id": f"{selected['cell']['run_id']}-block-result",
            "status": "finalized",
            "payload": selected,
            "payload_sha256": payload_sha256(selected),
        }
    )
    _write_exclusive(Path(output_path), artifact)
    return artifact


__all__ = [
    "SCHEMA",
    "S6BlockResultError",
    "build_s6_block_result",
    "finalize_s6_block_result",
    "verify_s6_block_result",
    "verify_s6_block_result_payload",
    "verify_s6_work_volume",
]
