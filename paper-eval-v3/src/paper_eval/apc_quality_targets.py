"""Sealed bridge from completed APC construction blocks to Quality v1."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy

from paper_eval.apc_aligned_baseline import APC_BASELINE_HISTORIES, APC_BASELINE_METHODS
from paper_eval.artifacts import payload_sha256


_RUN_ID = re.compile(r"^apc-baseline-[a-z0-9][a-z0-9-]{2,63}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")


def build_apc_quality_target_manifest(
    *, run_id: str, block_results: Sequence[Mapping[str, object]]
) -> dict[str, object]:
    if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
        raise ValueError("run id invalid")
    if isinstance(block_results, (str, bytes)) or not isinstance(block_results, Sequence):
        raise ValueError("block result inventory invalid")
    by_identity: dict[tuple[str, str], Mapping[str, object]] = {}
    for result in block_results:
        if not isinstance(result, Mapping) or result.get("status") != "PASS":
            raise ValueError("block result inventory invalid")
        identity = (
            str(result.get("method", "")).removesuffix("-aligned"),
            str(result.get("history_id", "")),
        )
        if identity in by_identity:
            raise ValueError("block result identity duplicate")
        by_identity[identity] = result
    expected = tuple(
        (method.removesuffix("-aligned"), history)
        for method in APC_BASELINE_METHODS
        for history in APC_BASELINE_HISTORIES
    )
    if set(by_identity) != set(expected):
        raise ValueError("block result inventory incomplete")
    targets: list[dict[str, object]] = []
    for identity in expected:
        value = by_identity[identity]
        digest = value.get("payload_sha256")
        namespace = value.get("namespace")
        episode_count = value.get("episode_count")
        if (
            not isinstance(digest, str)
            or _SHA.fullmatch(digest) is None
            or not isinstance(namespace, str)
            or not namespace
            or isinstance(episode_count, bool)
            or not isinstance(episode_count, int)
            or episode_count < 1
        ):
            raise ValueError("block result identity invalid")
        targets.append(
            {
                "method": identity[0],
                "history_id": identity[1],
                "namespace": namespace,
                "episode_count": episode_count,
                "construction_result_sha256": digest,
            }
        )
    body: dict[str, object] = {
        "schema_version": "membind.paper-eval-v3.apc-aligned-quality-targets.v1",
        "run_id": run_id,
        "target_count": len(targets),
        "targets": targets,
    }
    body["payload_sha256"] = payload_sha256(body)
    return body


def verify_apc_quality_target_manifest(value: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("quality target manifest invalid")
    candidate = deepcopy(dict(value))
    targets = candidate.get("targets")
    if isinstance(targets, (str, bytes)) or not isinstance(targets, Sequence):
        raise ValueError("quality target manifest invalid")
    synthetic = [
        {
            "status": "PASS",
            "method": f"{target.get('method')}-aligned",
            "history_id": target.get("history_id"),
            "namespace": target.get("namespace"),
            "episode_count": target.get("episode_count"),
            "payload_sha256": target.get("construction_result_sha256"),
        }
        for target in targets
        if isinstance(target, Mapping)
    ]
    expected = build_apc_quality_target_manifest(
        run_id=candidate.get("run_id"), block_results=synthetic
    )
    if candidate != expected:
        raise ValueError("quality target manifest drift")
    return candidate


__all__ = ["build_apc_quality_target_manifest", "verify_apc_quality_target_manifest"]
