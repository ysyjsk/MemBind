"""Read-only, offline verification for completed C2 evidence.

The verifier is intentionally independent from the C2 runner.  It verifies the
runner's immutable output before any analyzer or later stage consumes it, and it
never imports runtime configuration or service clients.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


MANIFEST_SCHEMA = "membind.native-characterization-c2-result.v1"
CHECKPOINT_SCHEMA = "membind.native-characterization-c2-checkpoint.v1"
BREAKDOWN_SCHEMA = "membind.native-characterization-e1-breakdown.v1"
BLOCK_SUMMARY_SCHEMA = "membind.native-characterization-c2-block-summary.v1"
TRACE_SCHEMA = "membind.native-characterization.trace.v1"
VERIFICATION_SCHEMA = "membind.native-characterization-c2-verification.v1"

_RUN_ID_RE = re.compile(r"^c2-[0-9a-f]{16}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_BLOCK_DIR_RE = re.compile(r"^(?P<index>[0-9]{3})_(?P<history>[0-9a-f]{8})$")
_ROOT_JSONL_SCHEMAS = {
    f"{name}.jsonl": f"membind.native-characterization-c2-{name}.v1"
    for name in ("spans", "llm", "embedding", "db", "events", "errors")
}
_REQUIRED_ROOT_ARTIFACTS = frozenset(
    {"checkpoint.json", "e1_breakdown.json", *_ROOT_JSONL_SCHEMAS}
)
_PROVENANCE_BINDINGS = {
    "phase_map_sha256": "artifacts/native_characterization/phase_map.json",
    "c2_runner_source_sha256": "src/native_characterization_c2.py",
    "u0_runtime_source_sha256": "src/native_characterization_runtime.py",
    "qwen_transport_source_sha256": "src/graphiti_native.py",
    "pinned_graphiti_openai_generic_source_sha256": (
        ".venv/lib/python3.12/site-packages/graphiti_core/llm_client/"
        "openai_generic_client.py"
    ),
    "measurement_adapter_source_sha256": (
        "src/native_characterization_c2_measurement.py"
    ),
    "base_instrumentation_source_sha256": (
        "src/native_characterization_instrumentation.py"
    ),
    "tracing_source_sha256": "src/native_characterization_tracing.py",
}


class C2VerificationError(RuntimeError):
    """A fail-closed verifier error containing only a fixed sanitized code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise C2VerificationError(code)


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


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _read_file(path: Path, code: str) -> bytes:
    if path.is_symlink():
        _fail(code)
    try:
        if not path.is_file():
            _fail(code)
        return path.read_bytes()
    except C2VerificationError:
        raise
    except OSError:
        _fail(code)


def _parse_json(raw: bytes, code: str) -> dict[str, Any]:
    def reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                _fail(code)
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("ascii"),
            object_pairs_hook=reject_duplicate_pairs,
            parse_constant=lambda _value: _fail(code),
        )
    except (UnicodeError, json.JSONDecodeError):
        _fail(code)
    if not isinstance(value, dict):
        _fail(code)
    return value


def _verify_payload_hash(value: Mapping[str, Any], code: str) -> None:
    candidate = dict(value)
    observed = candidate.pop("payload_sha256", None)
    if not _valid_sha256(observed):
        _fail(code)
    if observed != _sha256_bytes(_canonical_bytes(candidate)):
        _fail(code)


def _require_object(value: Any, code: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(code)
    return value


def _require_nonnegative_int(value: Any, code: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        _fail(code)
    return value


def _safe_artifact_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        _fail("artifact_path_invalid")
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or value != pure.as_posix()
        or any(part in {"", ".", ".."} for part in pure.parts)
        or pure.name == "manifest.json"
    ):
        _fail("artifact_path_invalid")
    return value


def _safe_provenance_file(validation_root: Path, value: Any) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        _fail("provenance_freeze_path_invalid")
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or value != pure.as_posix()
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        _fail("provenance_freeze_path_invalid")

    candidate = validation_root
    try:
        for part in pure.parts:
            candidate = candidate / part
            if candidate.is_symlink():
                _fail("provenance_freeze_path_invalid")
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(validation_root)
        if not resolved.is_file():
            _fail("provenance_local_file_missing")
    except C2VerificationError:
        raise
    except FileNotFoundError:
        _fail("provenance_local_file_missing")
    except (OSError, RuntimeError, ValueError):
        _fail("provenance_freeze_path_invalid")
    return resolved


def _expected_artifact_surface(
    indexed: set[str], block_count: int
) -> dict[int, str]:
    if not _REQUIRED_ROOT_ARTIFACTS.issubset(indexed):
        _fail("artifact_surface_invalid")
    block_dirs: dict[int, str] = {}
    for relative in indexed - set(_REQUIRED_ROOT_ARTIFACTS):
        parts = PurePosixPath(relative).parts
        if (
            len(parts) != 3
            or parts[0] != "blocks"
            or parts[2]
            not in {"trace.jsonl", "checkpoint.json", "block_summary.json"}
        ):
            _fail("artifact_surface_invalid")
        match = _BLOCK_DIR_RE.fullmatch(parts[1])
        if match is None:
            _fail("artifact_surface_invalid")
        index = int(match.group("index"))
        previous = block_dirs.setdefault(index, parts[1])
        if previous != parts[1]:
            _fail("artifact_surface_invalid")
    if set(block_dirs) != set(range(block_count)):
        _fail("artifact_surface_invalid")
    for directory in block_dirs.values():
        expected = {
            f"blocks/{directory}/trace.jsonl",
            f"blocks/{directory}/checkpoint.json",
            f"blocks/{directory}/block_summary.json",
        }
        if not expected.issubset(indexed):
            _fail("artifact_surface_invalid")
    expected_count = len(_REQUIRED_ROOT_ARTIFACTS) + 3 * block_count
    if len(indexed) != expected_count:
        _fail("artifact_surface_invalid")
    return block_dirs


def _actual_artifact_set(run_root: Path) -> set[str]:
    actual: set[str] = set()
    try:
        entries = list(run_root.rglob("*"))
    except OSError:
        _fail("artifact_set_mismatch")
    for path in entries:
        if path.is_symlink():
            _fail("artifact_set_mismatch")
        try:
            if path.is_file():
                relative = path.relative_to(run_root).as_posix()
                if relative != "manifest.json":
                    actual.add(relative)
            elif not path.is_dir():
                _fail("artifact_set_mismatch")
        except OSError:
            _fail("artifact_set_mismatch")
    return actual


def _verify_jsonl(
    *, relative: str, raw: bytes, run_id: str
) -> tuple[int, list[dict[str, Any]]]:
    if not raw or not raw.endswith(b"\n"):
        _fail("jsonl_line_format_invalid")
    physical_lines = raw.splitlines()
    if len(physical_lines) != raw.count(b"\n") or any(not line for line in physical_lines):
        _fail("jsonl_line_format_invalid")

    parts = PurePosixPath(relative).parts
    if len(parts) == 1 and relative in _ROOT_JSONL_SCHEMAS:
        expected_schema = _ROOT_JSONL_SCHEMAS[relative]
    elif len(parts) == 3 and parts[0] == "blocks" and parts[2] == "trace.jsonl":
        expected_schema = TRACE_SCHEMA
    else:
        _fail("jsonl_schema_invalid")

    envelopes: list[dict[str, Any]] = []
    for line in physical_lines:
        envelope = _parse_json(line, "jsonl_json_invalid")
        _verify_payload_hash(envelope, "jsonl_payload_hash_mismatch")
        if line != _canonical_bytes(envelope):
            _fail("jsonl_line_format_invalid")
        if envelope.get("schema_version") != expected_schema:
            _fail("jsonl_schema_invalid")
        if envelope.get("run_id") != run_id:
            _fail("jsonl_run_id_mismatch")
        envelopes.append(envelope)
    return len(physical_lines), envelopes


def _verify_indexed_json(relative: str, raw: bytes, run_id: str) -> dict[str, Any]:
    value = _parse_json(raw, "artifact_json_invalid")
    _verify_payload_hash(value, "artifact_payload_hash_mismatch")
    if raw != _canonical_bytes(value) + b"\n":
        _fail("artifact_json_format_invalid")
    name = PurePosixPath(relative).name
    if name == "checkpoint.json":
        expected_schema = CHECKPOINT_SCHEMA
    elif relative == "e1_breakdown.json":
        expected_schema = BREAKDOWN_SCHEMA
    elif name == "block_summary.json":
        expected_schema = BLOCK_SUMMARY_SCHEMA
    else:
        _fail("artifact_surface_invalid")
    if value.get("schema_version") != expected_schema:
        _fail("artifact_schema_invalid")
    if value.get("run_id") != run_id:
        _fail("artifact_run_id_mismatch")
    return value


def _verify_provenance(
    validation_root: Path,
    manifest: Mapping[str, Any],
) -> None:
    provenance = _require_object(manifest.get("provenance"), "provenance_invalid")
    freeze_path = _safe_provenance_file(
        validation_root,
        provenance.get("freeze_path"),
    )
    expected_freeze_sha = provenance.get("freeze_sha256")
    if not _valid_sha256(expected_freeze_sha):
        _fail("provenance_hash_invalid")
    freeze_raw = _read_file(freeze_path, "provenance_local_file_missing")
    if _sha256_bytes(freeze_raw) != expected_freeze_sha:
        _fail("provenance_local_hash_mismatch")
    freeze = _parse_json(freeze_raw, "provenance_freeze_invalid")
    policy = freeze.get("construction_compatibility_policy")
    if policy is None:
        freeze_mode = "json_schema"
    elif isinstance(policy, dict):
        freeze_mode = policy.get("structured_output_mode")
    else:
        _fail("structured_output_mode_cross_bind_mismatch")
    if (
        freeze_mode not in {"json_schema", "json_object"}
        or provenance.get("structured_output_mode") != freeze_mode
    ):
        _fail("structured_output_mode_cross_bind_mismatch")
    for field, relative in _PROVENANCE_BINDINGS.items():
        expected = provenance.get(field)
        if not _valid_sha256(expected):
            _fail("provenance_hash_invalid")
        raw = _read_file(validation_root / relative, "provenance_local_file_missing")
        if _sha256_bytes(raw) != expected:
            _fail("provenance_local_hash_mismatch")
    freeze_sha = manifest.get("freeze_sha256")
    if not _valid_sha256(freeze_sha) or freeze_sha != provenance.get("freeze_sha256"):
        _fail("freeze_cross_bind_mismatch")


def _verify_top_level_e1(
    *, validation_root: Path, manifest: Mapping[str, Any], run_id: str
) -> dict[str, Any]:
    expected = manifest.get("top_level_e1_breakdown_sha256")
    if not _valid_sha256(expected):
        _fail("top_level_e1_cross_bind_mismatch")
    path = validation_root / "artifacts/native_characterization/e1_breakdown.json"
    raw = _read_file(path, "top_level_e1_cross_bind_mismatch")
    if _sha256_bytes(raw) != expected:
        _fail("top_level_e1_cross_bind_mismatch")
    value = _parse_json(raw, "top_level_e1_json_invalid")
    _verify_payload_hash(value, "top_level_e1_payload_hash_mismatch")
    if raw != _canonical_bytes(value) + b"\n":
        _fail("top_level_e1_json_format_invalid")
    if value.get("schema_version") != BREAKDOWN_SCHEMA:
        _fail("top_level_e1_schema_invalid")
    if value.get("run_id") != run_id:
        _fail("top_level_e1_run_id_mismatch")
    return value


def verify_c2_run(validation_root: str | Path, run_id: str) -> dict[str, Any]:
    """Verify one completed C2 run without mutating any evidence."""

    if not isinstance(run_id, str) or _RUN_ID_RE.fullmatch(run_id) is None:
        _fail("run_id_invalid")
    try:
        validation = Path(validation_root).resolve(strict=True)
    except (OSError, RuntimeError):
        _fail("validation_root_invalid")
    if not validation.is_dir():
        _fail("validation_root_invalid")

    run_root = validation / "artifacts/native_characterization/runs" / run_id
    manifest_path = run_root / "manifest.json"
    manifest_raw = _read_file(manifest_path, "manifest_missing")
    manifest = _parse_json(manifest_raw, "manifest_json_invalid")
    _verify_payload_hash(manifest, "manifest_payload_hash_mismatch")
    if manifest_raw != _canonical_bytes(manifest) + b"\n":
        _fail("manifest_json_format_invalid")
    manifest_telemetry = _require_object(
        manifest.get("telemetry_completeness"), "manifest_contract_invalid"
    )
    if (
        manifest.get("schema_version") != MANIFEST_SCHEMA
        or manifest.get("run_id") != run_id
        or manifest.get("stage") != "C2"
        or manifest.get("status") != "completed"
        or manifest_telemetry.get("status") != "complete"
    ):
        _fail("manifest_contract_invalid")

    block_count = _require_nonnegative_int(
        manifest.get("block_count"), "manifest_contract_invalid"
    )
    episode_count = _require_nonnegative_int(
        manifest.get("episode_count"), "manifest_contract_invalid"
    )
    artifact_hashes = _require_object(
        manifest.get("artifact_sha256"), "artifact_index_invalid"
    )
    inventory = _require_object(
        manifest.get("artifact_inventory"), "artifact_index_invalid"
    )
    hash_keys = {_safe_artifact_path(key) for key in artifact_hashes}
    inventory_keys = {_safe_artifact_path(key) for key in inventory}
    if hash_keys != inventory_keys:
        _fail("artifact_index_mismatch")
    block_dirs = _expected_artifact_surface(hash_keys, block_count)
    if _actual_artifact_set(run_root) != hash_keys:
        _fail("artifact_set_mismatch")

    raw_artifacts: dict[str, bytes] = {}
    parsed_json: dict[str, dict[str, Any]] = {}
    jsonl_counts: dict[str, int] = {}
    for relative in sorted(hash_keys):
        expected_hash = artifact_hashes.get(relative)
        entry = _require_object(inventory.get(relative), "artifact_index_invalid")
        inventory_hash = entry.get("sha256")
        if not _valid_sha256(expected_hash) or not _valid_sha256(inventory_hash):
            _fail("artifact_index_invalid")
        if expected_hash != inventory_hash:
            _fail("artifact_index_mismatch")
        raw = _read_file(run_root / relative, "artifact_set_mismatch")
        raw_artifacts[relative] = raw
        if _sha256_bytes(raw) != expected_hash:
            _fail("artifact_hash_mismatch")
        byte_count = _require_nonnegative_int(
            entry.get("byte_count"), "artifact_byte_count_mismatch"
        )
        if byte_count != len(raw):
            _fail("artifact_byte_count_mismatch")
        if relative.endswith(".jsonl"):
            line_count = _require_nonnegative_int(
                entry.get("line_count"), "artifact_line_count_mismatch"
            )
            if line_count != raw.count(b"\n"):
                _fail("artifact_line_count_mismatch")
            line_count, _envelopes = _verify_jsonl(
                relative=relative, raw=raw, run_id=run_id
            )
            jsonl_counts[relative] = line_count
        else:
            if entry.get("line_count") is not None:
                _fail("artifact_line_count_mismatch")
            parsed_json[relative] = _verify_indexed_json(relative, raw, run_id)

    if any(jsonl_counts[name] != episode_count for name in _ROOT_JSONL_SCHEMAS):
        _fail("jsonl_line_count_cross_bind_mismatch")
    block_trace_total = sum(
        jsonl_counts[f"blocks/{directory}/trace.jsonl"]
        for directory in block_dirs.values()
    )
    if block_trace_total != episode_count:
        _fail("jsonl_line_count_cross_bind_mismatch")

    checkpoint_raw = raw_artifacts["checkpoint.json"]
    e1_raw = raw_artifacts["e1_breakdown.json"]
    if (
        not _valid_sha256(manifest.get("checkpoint_sha256"))
        or _sha256_bytes(checkpoint_raw) != manifest.get("checkpoint_sha256")
    ):
        _fail("checkpoint_cross_bind_mismatch")
    if (
        not _valid_sha256(manifest.get("e1_breakdown_sha256"))
        or _sha256_bytes(e1_raw) != manifest.get("e1_breakdown_sha256")
    ):
        _fail("e1_cross_bind_mismatch")

    checkpoint = parsed_json["checkpoint.json"]
    e1 = parsed_json["e1_breakdown.json"]
    completed_blocks = checkpoint.get("completed_block_indices")
    completed_episodes = checkpoint.get("completed_episode_ids")
    if (
        checkpoint.get("stage") != "C2"
        or checkpoint.get("status") != "completed"
        or not isinstance(completed_blocks, list)
        or not isinstance(completed_episodes, list)
        or len(completed_blocks) != block_count
        or len(completed_episodes) != episode_count
    ):
        _fail("checkpoint_contract_invalid")
    e1_telemetry = _require_object(
        e1.get("telemetry_completeness"), "e1_contract_invalid"
    )
    if (
        e1.get("freeze_sha256") != manifest.get("freeze_sha256")
        or e1_telemetry.get("status") != "complete"
    ):
        _fail("e1_contract_invalid")

    _verify_provenance(validation, manifest)
    top_level_e1 = _verify_top_level_e1(
        validation_root=validation, manifest=manifest, run_id=run_id
    )
    if top_level_e1.get("payload_sha256") != e1.get("payload_sha256"):
        _fail("top_level_e1_cross_bind_mismatch")

    return {
        "schema_version": VERIFICATION_SCHEMA,
        "status": "verified",
        "run_id": run_id,
        "manifest_sha256": _sha256_bytes(manifest_raw),
        "indexed_file_count": len(hash_keys),
        "jsonl_line_count": sum(jsonl_counts.values()),
        "checkpoint_sha256": manifest["checkpoint_sha256"],
        "e1_breakdown_sha256": manifest["e1_breakdown_sha256"],
        "top_level_e1_breakdown_sha256": manifest[
            "top_level_e1_breakdown_sha256"
        ],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validation-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)
    try:
        result = verify_c2_run(args.validation_root, args.run_id)
    except C2VerificationError as exc:
        print(
            json.dumps(
                {"status": "error", "error_code": exc.code},
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
