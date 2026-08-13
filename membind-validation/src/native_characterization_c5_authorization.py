"""One-way, fail-closed authorization of the frozen C5 live screening."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping


FREEZE_RELATIVE_PATH = "artifacts/native_characterization/freeze_reference_aligned_64k.json"
WORKPLAN_NAME = "MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.1.md"
FROZEN_HISTORY_ID = "07741c45"
FROZEN_EPISODE_COUNT = 49
FROZEN_CONCURRENCY_GRID = [1, 2, 4, 8]
FROZEN_NAMESPACES = [
    "nc-e4-1434fcb947df5c3d",
    "nc-e4-b352061ffa0d4b21",
    "nc-e4-c15538d1fe2801cb",
    "nc-e4-2a427029b1a8b2ac",
]


class C5AuthorizationError(RuntimeError):
    """Sanitized C5 authority-transition failure."""


def _fail(code: str) -> None:
    raise C5AuthorizationError(code)


def _sha(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        _fail("evidence_unreadable")


def _load(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text("ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail(code)
    if not isinstance(value, dict):
        _fail(code)
    return value


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    raw = (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _freeze_contract(freeze: Mapping[str, Any]) -> dict[str, Any]:
    protocol = freeze.get("protocol")
    screening = freeze.get("screening")
    e4 = screening.get("e4") if isinstance(screening, Mapping) else None
    dataset = freeze.get("dataset")
    histories = dataset.get("calibration_histories") if isinstance(dataset, Mapping) else None
    history = next(
        (
            item
            for item in histories or []
            if isinstance(item, Mapping) and item.get("history_id") == FROZEN_HISTORY_ID
        ),
        None,
    )
    blocks = e4.get("block_order") if isinstance(e4, Mapping) else None
    namespaces = (
        [item.get("graph_namespace") for item in blocks]
        if isinstance(blocks, list) and all(isinstance(item, Mapping) for item in blocks)
        else None
    )
    if (
        not isinstance(protocol, Mapping)
        or protocol.get("id") != "native-characterization-v1.1"
        or protocol.get("freeze_marker") is not True
        or not isinstance(e4, Mapping)
        or e4.get("history_id") != FROZEN_HISTORY_ID
        or e4.get("concurrency_order") != FROZEN_CONCURRENCY_GRID
        or namespaces != FROZEN_NAMESPACES
        or not isinstance(history, Mapping)
        or history.get("episode_count") != FROZEN_EPISODE_COUNT
        or len(history.get("episodes", [])) != FROZEN_EPISODE_COUNT
    ):
        _fail("freeze_contract_mismatch")
    return {
        "history_id": FROZEN_HISTORY_ID,
        "episode_count": FROZEN_EPISODE_COUNT,
        "episode_source_hashes": [
            item.get("episode_source_sha256") for item in history["episodes"]
        ],
        "concurrency_grid": FROZEN_CONCURRENCY_GRID,
        "graph_namespaces": FROZEN_NAMESPACES,
        "screening_pass_count": 1,
        "workplan_sha256": protocol.get("workplan_sha256"),
    }


def _is_exact_source(state: Mapping[str, Any]) -> bool:
    return (
        state.get("protocol_version") == "current-validation-v1.3"
        and state.get("current_stage") == "NATIVE_CHARACTERIZATION"
        and state.get("current_action_scope") == "native_characterization_c4_live_only"
        and state.get("status") == "native_characterization_c4_live_only"
        and state.get("authorized_live_actions") == ["native_characterization_c4"]
        and state.get("next_allowed_action") == "run_native_characterization_c4"
        and state.get("native_characterization_live_authorized") is True
        and state.get("service_admin_authorized") is False
    )


def _is_exact_target(state: Mapping[str, Any]) -> bool:
    return (
        state.get("current_action_scope") == "native_characterization_c5_live_only"
        and state.get("status") == "native_characterization_c5_live_only"
        and state.get("authorized_live_actions") == ["native_characterization_c5"]
        and state.get("next_allowed_action") == "run_native_characterization_c5"
        and state.get("native_characterization_live_authorized") is True
        and state.get("service_admin_authorized") is False
        and isinstance(state.get("native_characterization_c5_authorization"), Mapping)
    )


def authorize_c5(
    *,
    validation_root: str | Path,
    state_path: str | Path,
    c4_summary_path: str | Path,
    c4_summary_sha256: str,
) -> dict[str, Any]:
    """Atomically replace exact C4-only authority with exact C5-only authority."""

    validation = Path(validation_root).resolve(strict=True)
    state_file = Path(state_path).resolve(strict=True)
    c4_path = Path(c4_summary_path).resolve(strict=True)
    if state_file != validation / "CURRENT_STATE.json":
        _fail("path_not_exact")
    try:
        c4_path.relative_to(validation / "artifacts/native_characterization/runs")
    except ValueError:
        _fail("path_not_exact")
    if not isinstance(c4_summary_sha256, str) or _sha(c4_path) != c4_summary_sha256:
        _fail("c4_summary_hash_mismatch")

    freeze_path = validation / FREEZE_RELATIVE_PATH
    workplan_path = validation.parent / WORKPLAN_NAME
    freeze = _load(freeze_path, "freeze_unreadable")
    contract = _freeze_contract(freeze)
    if _sha(workplan_path) != contract["workplan_sha256"]:
        _fail("workplan_hash_mismatch")
    c4 = _load(c4_path, "c4_summary_unreadable")
    if (
        c4.get("status") != "complete"
        or c4.get("block_count") != 10
        or c4.get("episode_count") != 490
        or not isinstance(c4.get("payload_sha256"), str)
    ):
        _fail("c4_summary_not_complete")

    state = _load(state_file, "state_unreadable")
    if _is_exact_target(state):
        evidence = state["native_characterization_c5_authorization"]
        if (
            evidence.get("c4_summary_sha256") != c4_summary_sha256
            or evidence.get("freeze_sha256") != _sha(freeze_path)
        ):
            _fail("source_state_not_exact")
        return {"status": "authorized"}
    if not _is_exact_source(state):
        _fail("source_state_not_exact")

    evidence = {
        "schema_version": "membind.native-characterization-c5-authorization.v1",
        **contract,
        "freeze_path": FREEZE_RELATIVE_PATH,
        "freeze_sha256": _sha(freeze_path),
        "c4_summary_path": c4_path.relative_to(validation).as_posix(),
        "c4_summary_sha256": c4_summary_sha256,
        "c4_summary_payload_sha256": c4["payload_sha256"],
        "live_authorized": True,
    }
    target = deepcopy(state)
    target.update(
        {
            "current_action_scope": "native_characterization_c5_live_only",
            "status": "native_characterization_c5_live_only",
            "authorized_live_actions": ["native_characterization_c5"],
            "next_allowed_action": "run_native_characterization_c5",
            "native_characterization_live_authorized": True,
            "service_admin_authorized": False,
            "native_characterization_c5_authorization": evidence,
        }
    )
    _atomic_write(state_file, target)
    return {"status": "authorized"}
