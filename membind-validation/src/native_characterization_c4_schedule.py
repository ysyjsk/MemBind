"""Derive the frozen C4/E3 open-loop schedules from retained C2 evidence.

This module is deliberately offline-only.  It verifies and reads immutable C2
artifacts, performs exact rational arithmetic, and returns (or prints) a dry-run
dictionary.  It has no service clients and no artifact-writing mode.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from fractions import Fraction
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from native_characterization_c2_verify import C2VerificationError, verify_c2_run


SCHEDULE_SCHEMA = "membind.native-characterization-c4-schedule-dry-run.v1"
E1_SCHEMA = "membind.native-characterization-e1-breakdown.v1"
FROZEN_E3_HISTORY_ID = "07741c45"
FROZEN_LOADS: tuple[tuple[float, Fraction], ...] = (
    (0.5, Fraction(1, 2)),
    (0.8, Fraction(4, 5)),
    (1.0, Fraction(1, 1)),
    (1.2, Fraction(6, 5)),
    (1.5, Fraction(3, 2)),
)
FROZEN_METHODS = ("Native-Sync", "Native-Async-Serial")
FREEZE_SCHEMA = "membind.native-characterization-freeze.v1"
MANIFEST_SCHEMA = "membind.native-characterization-c2-result.v1"
_GRAPH_NAMESPACE_RE = re.compile(r"^nc-e3-[0-9a-f]{16}$")


class NativeCharacterizationC4ScheduleError(RuntimeError):
    """A fail-closed offline schedule derivation error."""


def _fail(code: str) -> None:
    raise NativeCharacterizationC4ScheduleError(code)


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError):
        _fail("payload_not_canonical_json")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def payload_sha256(value: Mapping[str, Any]) -> str:
    """Return the canonical payload seal, excluding an existing seal field."""

    candidate = dict(value)
    candidate.pop("payload_sha256", None)
    return _sha256(_canonical_bytes(candidate))


def round_fraction_half_up(value: Fraction) -> int:
    """Round one non-negative exact fraction to integer nanoseconds."""

    if not isinstance(value, Fraction) or value < 0:
        _fail("rounding_input_invalid")
    quotient, remainder = divmod(value.numerator, value.denominator)
    return quotient + int(2 * remainder >= value.denominator)


def _read_verified_e1(
    validation: Path,
    verification: Mapping[str, Any],
    run_id: str,
) -> tuple[dict[str, Any], str]:
    path = validation / "artifacts/native_characterization/runs" / run_id / "e1_breakdown.json"
    if path.is_symlink():
        _fail("e1_path_invalid")
    try:
        raw = path.read_bytes()
    except OSError:
        _fail("e1_unreadable")
    observed_sha256 = _sha256(raw)
    if observed_sha256 != verification.get("e1_breakdown_sha256"):
        _fail("e1_verification_binding_mismatch")
    try:
        value = json.loads(raw.decode("ascii"))
    except (UnicodeError, json.JSONDecodeError):
        _fail("e1_json_invalid")
    if not isinstance(value, dict):
        _fail("e1_json_invalid")
    if raw != _canonical_bytes(value) + b"\n":
        _fail("e1_not_canonical")
    if (
        value.get("schema_version") != E1_SCHEMA
        or value.get("run_id") != run_id
        or value.get("payload_sha256") != payload_sha256(value)
    ):
        _fail("e1_contract_invalid")
    return value, observed_sha256


def _read_verified_freeze(
    validation: Path,
    verification: Mapping[str, Any],
    run_id: str,
) -> tuple[dict[str, Any], str, str, list[dict[str, Any]]]:
    manifest_path = (
        validation / "artifacts/native_characterization/runs" / run_id / "manifest.json"
    )
    if manifest_path.is_symlink():
        _fail("manifest_path_invalid")
    try:
        manifest_raw = manifest_path.read_bytes()
        manifest = json.loads(manifest_raw.decode("ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail("manifest_unreadable")
    if (
        not isinstance(manifest, dict)
        or manifest_raw != _canonical_bytes(manifest) + b"\n"
        or _sha256(manifest_raw) != verification.get("manifest_sha256")
        or manifest.get("schema_version") != MANIFEST_SCHEMA
        or manifest.get("status") != "completed"
        or manifest.get("run_id") != run_id
        or manifest.get("payload_sha256") != payload_sha256(manifest)
    ):
        _fail("manifest_contract_invalid")
    provenance = manifest.get("provenance")
    if not isinstance(provenance, Mapping):
        _fail("manifest_provenance_invalid")
    freeze_relative = provenance.get("freeze_path")
    freeze_sha256 = provenance.get("freeze_sha256")
    if (
        not isinstance(freeze_relative, str)
        or not freeze_relative
        or "\\" in freeze_relative
        or not isinstance(freeze_sha256, str)
        or manifest.get("freeze_sha256") != freeze_sha256
    ):
        _fail("freeze_binding_invalid")
    pure = PurePosixPath(freeze_relative)
    if (
        pure.is_absolute()
        or freeze_relative != pure.as_posix()
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        _fail("freeze_binding_invalid")
    freeze_path = validation.joinpath(*pure.parts)
    if freeze_path.is_symlink():
        _fail("freeze_path_invalid")
    try:
        resolved = freeze_path.resolve(strict=True)
        resolved.relative_to(validation)
        freeze_raw = resolved.read_bytes()
        freeze = json.loads(freeze_raw.decode("ascii"))
    except (OSError, RuntimeError, ValueError, UnicodeError, json.JSONDecodeError):
        _fail("freeze_unreadable")
    if (
        not isinstance(freeze, dict)
        or freeze_raw != _canonical_bytes(freeze) + b"\n"
        or _sha256(freeze_raw) != freeze_sha256
        or freeze.get("schema_version") != FREEZE_SCHEMA
        or freeze.get("payload_sha256") != payload_sha256(freeze)
    ):
        _fail("freeze_contract_invalid")

    e3 = freeze.get("screening", {}).get("e3")
    block_order = e3.get("block_order") if isinstance(e3, Mapping) else None
    expected_pairs = [
        (method, label)
        for method in FROZEN_METHODS
        for label, _fraction in FROZEN_LOADS
    ]
    if (
        not isinstance(e3, Mapping)
        or e3.get("history_id") != FROZEN_E3_HISTORY_ID
        or e3.get("normalized_offered_load_order")
        != [label for label, _fraction in FROZEN_LOADS]
        or not isinstance(block_order, list)
        or len(block_order) != len(expected_pairs)
    ):
        _fail("frozen_e3_selection_invalid")
    normalized_blocks: list[dict[str, Any]] = []
    seen_namespaces: set[str] = set()
    for index, (item, expected_pair) in enumerate(zip(block_order, expected_pairs)):
        if not isinstance(item, Mapping):
            _fail("frozen_e3_block_invalid")
        namespace = item.get("graph_namespace")
        pair = (item.get("method"), item.get("normalized_offered_load"))
        if (
            item.get("block_index") != index
            or pair != expected_pair
            or not isinstance(namespace, str)
            or _GRAPH_NAMESPACE_RE.fullmatch(namespace) is None
            or namespace in seen_namespaces
        ):
            _fail("frozen_e3_block_invalid")
        seen_namespaces.add(namespace)
        normalized_blocks.append(dict(item))
    return freeze, freeze_relative, freeze_sha256, normalized_blocks


def _select_history_block(
    e1: Mapping[str, Any], history_id: str
) -> tuple[dict[str, Any], list[str], list[int]]:
    blocks = e1.get("blocks")
    if not isinstance(blocks, list):
        _fail("e1_blocks_invalid")
    matches = [item for item in blocks if isinstance(item, dict) and item.get("history_id") == history_id]
    if len(matches) != 1:
        _fail("e1_history_block_invalid")
    block = matches[0]
    episode_count = block.get("episode_count")
    observed_count = block.get("observed_episode_count")
    block_index = block.get("block_index")
    metrics = block.get("episode_metrics")
    telemetry = block.get("telemetry_completeness")
    if (
        not isinstance(episode_count, int)
        or isinstance(episode_count, bool)
        or episode_count <= 0
        or observed_count != episode_count
        or not isinstance(block_index, int)
        or isinstance(block_index, bool)
        or block_index < 0
        or not isinstance(metrics, list)
        or len(metrics) != episode_count
        or not isinstance(telemetry, Mapping)
        or telemetry.get("status") != "complete"
    ):
        _fail("e1_history_block_invalid")

    ordered = sorted(
        metrics,
        key=lambda item: item.get("source_sequence", -1) if isinstance(item, dict) else -1,
    )
    episode_ids: list[str] = []
    service_times: list[int] = []
    for expected_sequence, item in enumerate(ordered):
        if not isinstance(item, dict):
            _fail("e1_episode_metric_invalid")
        expected_id = f"{history_id}:{expected_sequence}"
        service_ns = item.get("service_latency_ns")
        item_telemetry = item.get("telemetry_completeness")
        if (
            item.get("source_sequence") != expected_sequence
            or item.get("episode_id") != expected_id
            or not isinstance(service_ns, int)
            or isinstance(service_ns, bool)
            or service_ns <= 0
            or not isinstance(item_telemetry, Mapping)
            or item_telemetry.get("status") != "complete"
        ):
            _fail("e1_episode_metric_invalid")
        episode_ids.append(expected_id)
        service_times.append(service_ns)

    sum_service_ns = sum(service_times)
    accounting = block.get("accounting")
    if (
        not isinstance(accounting, Mapping)
        or block.get("total_add_episode_union_ns") != sum_service_ns
        or accounting.get("interval_union_ns") != sum_service_ns
        or accounting.get("inclusive_ns") != sum_service_ns
    ):
        _fail("e1_service_sum_cross_check_failed")
    return block, episode_ids, service_times


def _load_schedules(sum_service_ns: int, episode_count: int) -> list[dict[str, Any]]:
    service_reference = Fraction(sum_service_ns, episode_count)
    schedules: list[dict[str, Any]] = []
    for label, normalized_load in FROZEN_LOADS:
        exact_interarrival = service_reference / normalized_load
        interarrival_ns = round_fraction_half_up(exact_interarrival)
        schedules.append(
            {
                "normalized_offered_load": label,
                "interarrival_exact_ns": {
                    "numerator": exact_interarrival.numerator,
                    "denominator": exact_interarrival.denominator,
                },
                "interarrival_ns": interarrival_ns,
                "interarrival_seconds": f"{interarrival_ns / 1_000_000_000:.9f}",
                "absolute_arrival_offsets_ns": [
                    sequence * interarrival_ns for sequence in range(episode_count)
                ],
            }
        )
    return schedules


def derive_c4_schedule(
    validation_root: str | Path,
    run_id: str,
    history_id: str = FROZEN_E3_HISTORY_ID,
) -> dict[str, Any]:
    """Return the deterministic dry-run schedule for the frozen E3 history."""

    if history_id != FROZEN_E3_HISTORY_ID:
        _fail("history_not_frozen_e3_history")
    try:
        validation = Path(validation_root).resolve(strict=True)
    except (OSError, RuntimeError):
        _fail("validation_root_invalid")
    if not validation.is_dir():
        _fail("validation_root_invalid")

    try:
        verification = verify_c2_run(validation, run_id)
    except C2VerificationError as exc:
        _fail(f"c2_verification_failed:{exc.code}")
    if verification.get("status") != "verified" or verification.get("run_id") != run_id:
        _fail("c2_verification_mismatch")

    freeze, freeze_relative, freeze_sha256, frozen_blocks = _read_verified_freeze(
        validation, verification, run_id
    )
    e1, e1_sha256 = _read_verified_e1(validation, verification, run_id)
    block, episode_ids, service_times = _select_history_block(e1, history_id)
    sum_service_ns = sum(service_times)
    episode_count = len(service_times)
    s_ref = Fraction(sum_service_ns, episode_count)
    load_schedules = _load_schedules(sum_service_ns, episode_count)
    schedule_by_load = {
        item["normalized_offered_load"]: item for item in load_schedules
    }
    block_schedules = [
        {
            **block,
            "interarrival_ns": schedule_by_load[block["normalized_offered_load"]][
                "interarrival_ns"
            ],
            "absolute_arrival_offsets_ns": schedule_by_load[
                block["normalized_offered_load"]
            ]["absolute_arrival_offsets_ns"],
        }
        for block in frozen_blocks
    ]
    result: dict[str, Any] = {
        "schema_version": SCHEDULE_SCHEMA,
        "status": "dry_run",
        "stage": "C4/E3_OFFLINE_SCHEDULE",
        "run_id": run_id,
        "history_id": history_id,
        "rounding_rule": "exact_fraction_round_half_up_to_integer_ns",
        "schedule_semantics": "controlled_deterministic_absolute_open_loop_replay",
        "service_reference": {
            "episode_count": episode_count,
            "sum_service_ns": sum_service_ns,
            "S_ref_exact_ns": {
                "numerator": s_ref.numerator,
                "denominator": s_ref.denominator,
            },
            "S_ref_rounded_ns": round_fraction_half_up(s_ref),
        },
        "episode_ids": episode_ids,
        "load_schedules": load_schedules,
        "block_schedules": block_schedules,
        "provenance": {
            "c2_verification": dict(verification),
            "freeze_path": freeze_relative,
            "freeze_sha256": freeze_sha256,
            "freeze_payload_sha256": freeze["payload_sha256"],
            "e1_breakdown_sha256": e1_sha256,
            "e1_breakdown_payload_sha256": e1["payload_sha256"],
            "e1_block_index": block["block_index"],
            "episode_service_field": "service_latency_ns",
            "block_service_cross_check_field": "total_add_episode_union_ns",
        },
    }
    result["payload_sha256"] = payload_sha256(result)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validation-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--history-id", default=FROZEN_E3_HISTORY_ID)
    args = parser.parse_args(argv)
    try:
        result = derive_c4_schedule(args.validation_root, args.run_id, args.history_id)
    except NativeCharacterizationC4ScheduleError as exc:
        print(
            json.dumps(
                {"status": "error", "error_code": str(exc)},
                ensure_ascii=True,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
