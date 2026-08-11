"""Build the deterministic, content-free Native characterization freeze.

This module performs offline identity verification only.  It never loads
credentials or clients and persists hashes of calibration input rather than
the underlying episode text.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from dataset import build_episodes


WORKPLAN_ID = "native-characterization-v1.1"
WORKPLAN_NAME = "MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.1.md"
WORKPLAN_SHA256 = "be3112cc2da4080ce98f9c94f1ab510ba5cc8350dca108a15e304da04c996b5b"
SPLIT_SHA256 = "747946a8792422ea35e9d56b864efb1a137cb6eb8a8e16f97808fe86f938c091"
SOURCE_SHA256 = "d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442"
CALIBRATION_IDS = ("07741c45", "b6019101", "6071bd76", "a2f3aa27")
GRAPHITI_COMMIT = "021d3a57d511f21b10adaf7fa923bd5c1fce5e9d"
CONSTRUCTION_REVISION = "6e2312b85c2ae9a31f629f24493b79d8b02eab1a"
STATE_SOURCE_SHA256 = "fb57c0edb6388c2ae94c6ba338e1671c39fa08e218cfc96566ee4d315b2e231d"
OFFLINE_TRANSITION_STATE_SHA256 = (
    "af7651fb8d5e5f6e4b6b43fe028969ce45182387326c162bcd8d45df0b47b731"
)

_CONSTRUCTION_EVIDENCE = "artifacts/environment/v3_construction_runtime_evidence_20260809.json"
_EMBEDDING_EVIDENCE = "artifacts/environment/embedding_model_fingerprint.json"
_NEO4J_STATUS_EVIDENCE = "artifacts/environment/neo4j_daemon_status.json"
_NEO4J_PREFLIGHT_EVIDENCE = "artifacts/environment/v3_neo4j_preflight_20260808.json"
_EXPECTED_EVIDENCE_HASHES = {
    _CONSTRUCTION_EVIDENCE: "72ccd3757aa384398b37d6c9a050c730d9bd35843addce68baa8089c8fd9595e",
    _EMBEDDING_EVIDENCE: "389fb4c9cf87217c333741170c9162cf7353cb05026de510685b27fa336299d0",
    _NEO4J_STATUS_EVIDENCE: "4834e16ac35a548ab391b4e5afb75b4ea866ddd309341368148472787787af43",
    _NEO4J_PREFLIGHT_EVIDENCE: "6dc02ed4493a75aed25cfdd28101a198dc3f27aac54951e5f0d87c5fb11511b8",
}


def canonical_bytes(value: Any) -> bytes:
    """Return the one ASCII JSON representation used for identities."""

    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError(f"{label} is unreadable") from None
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _load_source_records(path: Path) -> list[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("source is unreadable") from None
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError("source must be a list of objects")
    return value


def _verify_graphiti_installation() -> tuple[str, str]:
    distribution = importlib.metadata.distribution("graphiti-core")
    version = distribution.version
    direct_url_text = distribution.read_text("direct_url.json")
    try:
        direct_url = json.loads(direct_url_text or "{}")
        commit = direct_url["vcs_info"]["commit_id"]
    except (KeyError, TypeError, json.JSONDecodeError):
        raise ValueError("Graphiti installation has no immutable commit identity") from None
    if version != "0.29.3" or commit != GRAPHITI_COMMIT:
        raise ValueError("Graphiti installation identity mismatch")
    return version, commit


def _verify_evidence(validation_root: Path) -> dict[str, dict[str, Any]]:
    loaded: dict[str, dict[str, Any]] = {}
    for relative, expected in _EXPECTED_EVIDENCE_HASHES.items():
        path = validation_root / relative
        if _sha256_file(path) != expected:
            raise ValueError(f"runtime evidence hash mismatch: {relative}")
        loaded[relative] = _load_object(path, relative)

    construction = loaded[_CONSTRUCTION_EVIDENCE].get("runtime")
    if not isinstance(construction, dict) or (
        construction.get("served_model_name") != "qwen3-32b-fp8"
        or construction.get("vllm_version") != "0.26.0"
        or construction.get("max_model_len") != 40960
        or construction.get("dtype") != "bfloat16"
        or construction.get("quantization") != "fp8"
        or construction.get("default_chat_template_kwargs", {}).get("enable_thinking")
        is not False
    ):
        raise ValueError("construction runtime identity mismatch")

    embedding = loaded[_EMBEDDING_EVIDENCE]
    namespace = embedding.get("namespace")
    if embedding.get("gate_status") != "pass" or not isinstance(namespace, dict) or (
        namespace.get("served_model_id") != "qwen3-embedding-0.6b"
        or namespace.get("dimension") != 1024
        or namespace.get("dtype") != "bfloat16"
        or namespace.get("pooling") != "last_token"
        or namespace.get("normalization") != "l2"
        or namespace.get("instruction_policy") != "none"
    ):
        raise ValueError("embedding runtime identity mismatch")

    neo4j_status = loaded[_NEO4J_STATUS_EVIDENCE]
    neo4j_preflight = loaded[_NEO4J_PREFLIGHT_EVIDENCE]
    if (
        neo4j_status.get("ok") is not True
        or not str(neo4j_status.get("neo4j_home", "")).endswith("neo4j-community-5.26.0")
        or neo4j_preflight.get("ok") is not True
        or neo4j_preflight.get("uri") != "bolt://localhost:7687"
    ):
        raise ValueError("Neo4j historical identity mismatch")
    return loaded


def _calibration_histories(
    source_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    selected = {
        str(record.get("question_id")): record
        for record in source_records
        if str(record.get("question_id")) in CALIBRATION_IDS
    }
    if set(selected) != set(CALIBRATION_IDS):
        raise ValueError("calibration history missing from source")

    histories: list[dict[str, Any]] = []
    for history_id in CALIBRATION_IDS:
        episodes = build_episodes(selected[history_id])
        prefix_hashes: list[str] = []
        episode_rows: list[dict[str, Any]] = []
        for episode in episodes:
            prefix_hashes.append(episode.source_hash)
            episode_rows.append(
                {
                    "source_sequence": episode.source_sequence,
                    "episode_source_sha256": episode.source_hash,
                    "prefix_sha256": _sha256_bytes(canonical_bytes(prefix_hashes)),
                }
            )
        histories.append(
            {
                "history_id": history_id,
                "episode_count": len(episode_rows),
                "episodes": episode_rows,
            }
        )
    return histories


def _graph_namespace(stage: str, block: Mapping[str, Any]) -> str:
    """Derive one Graphiti-safe namespace before any treatment outcome exists."""

    identity = {
        "workplan_sha256": WORKPLAN_SHA256,
        "stage": stage,
        "block": dict(block),
    }
    suffix = _sha256_bytes(canonical_bytes(identity))[:16]
    return f"nc-{stage}-{suffix}"


def _seal(payload: Mapping[str, Any]) -> dict[str, Any]:
    sealed = deepcopy(dict(payload))
    sealed["payload_sha256"] = _sha256_bytes(canonical_bytes(sealed))
    return sealed


def validate_artifact(artifact: Mapping[str, Any]) -> None:
    """Verify the embedded payload identity without accepting self-hash drift."""

    if not isinstance(artifact, Mapping):
        raise ValueError("artifact must be an object")
    candidate = deepcopy(dict(artifact))
    observed = candidate.pop("payload_sha256", None)
    expected = _sha256_bytes(canonical_bytes(candidate))
    if observed != expected:
        raise ValueError("payload_sha256 mismatch")


def build_artifacts(
    *,
    repo_root: str | Path,
    validation_root: str | Path,
    source_path: str | Path,
    split_path: str | Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build freeze and phase-map payloads after verifying every local input."""

    repo = Path(repo_root).resolve()
    validation = Path(validation_root).resolve()
    source = Path(source_path).resolve()
    split_file = (
        Path(split_path).resolve()
        if split_path is not None
        else validation / "artifacts/dataset/frozen_split_v1_3.json"
    )
    workplan = repo / WORKPLAN_NAME
    if _sha256_file(workplan) != WORKPLAN_SHA256:
        raise ValueError("workplan hash mismatch")
    if _sha256_file(split_file) != SPLIT_SHA256 and split_path is None:
        raise ValueError("split hash mismatch")

    split = _load_object(split_file, "split")
    if _sha256_file(source) != split.get("source_sha256"):
        raise ValueError("source hash does not match split")
    if split.get("source_sha256") != SOURCE_SHA256:
        raise ValueError("source hash does not match frozen identity")
    if list(split.get("calibration_question_ids", [])) != list(CALIBRATION_IDS):
        raise ValueError("calibration IDs do not match frozen order")

    current_state_path = validation / "CURRENT_STATE.json"
    state = _load_object(current_state_path, "current state")
    transition = state.get("native_characterization_transition")
    if not isinstance(transition, dict) or (
        transition.get("source_state_sha256") != STATE_SOURCE_SHA256
        or transition.get("workplan_sha256") != WORKPLAN_SHA256
        or transition.get("live_authorized") is not False
    ):
        raise ValueError("Native characterization state transition mismatch")

    evidence = _verify_evidence(validation)
    graphiti_version, graphiti_commit = _verify_graphiti_installation()
    histories = _calibration_histories(_load_source_records(source))
    first_history = histories[0]
    first_episode = first_history["episodes"][0]

    c0_block = {
        "history_id": first_history["history_id"],
        "source_sequence": first_episode["source_sequence"],
        "episode_source_sha256": first_episode["episode_source_sha256"],
    }
    c0_block["graph_namespace"] = _graph_namespace("c0", c0_block)
    e1_e2_blocks = [
        {"block_index": index, "history_id": history_id}
        for index, history_id in enumerate(CALIBRATION_IDS)
    ]
    for block in e1_e2_blocks:
        block["graph_namespace"] = _graph_namespace("e1e2", block)
    e3_blocks = [
        {
            "block_index": index,
            "method": method,
            "normalized_offered_load": load,
        }
        for index, (method, load) in enumerate(
            (method, load)
            for method in ("Native-Sync", "Native-Async-Serial")
            for load in (0.5, 0.8, 1.0, 1.2, 1.5)
        )
    ]
    for block in e3_blocks:
        block["graph_namespace"] = _graph_namespace("e3", block)
    e4_blocks = [
        {"block_index": index, "concurrency": concurrency}
        for index, concurrency in enumerate((1, 2, 4, 8))
    ]
    for block in e4_blocks:
        block["graph_namespace"] = _graph_namespace("e4", block)

    source_files = {
        "offline_transition_state_sha256": OFFLINE_TRANSITION_STATE_SHA256,
        "instrumentation_sha256": _sha256_file(
            validation / "src/native_characterization_instrumentation.py"
        ),
        "u0_runtime_source_sha256": _sha256_file(
            validation / "src/native_characterization_runtime.py"
        ),
        "c0_runner_source_sha256": _sha256_file(
            validation / "src/native_characterization_c0.py"
        ),
        "split_sha256": _sha256_file(split_file),
        "workplan_sha256": WORKPLAN_SHA256,
        **{
            relative: digest for relative, digest in _EXPECTED_EVIDENCE_HASHES.items()
        },
    }
    construction = evidence[_CONSTRUCTION_EVIDENCE]["runtime"]
    embedding = evidence[_EMBEDDING_EVIDENCE]["namespace"]

    freeze = _seal(
        {
            "schema_version": "membind.native-characterization-freeze.v1",
            "artifact_id": "native-characterization-freeze",
            "run_id": "native-characterization-freeze",
            "creation_command": (
                ".venv/bin/python src/native_characterization_freeze.py --write"
            ),
            "protocol": {
                "id": WORKPLAN_ID,
                "workplan_path": WORKPLAN_NAME,
                "workplan_sha256": WORKPLAN_SHA256,
                "freeze_marker": True,
            },
            "state_transition": {
                "source_state_sha256": transition["source_state_sha256"],
                "transition_schema_version": transition["schema_version"],
                "offline_transition_state_sha256": (
                    OFFLINE_TRANSITION_STATE_SHA256
                ),
                "live_authorized": False,
            },
            "dataset": {
                "source_sha256": SOURCE_SHA256,
                "split_sha256": source_files["split_sha256"],
                "calibration_histories": histories,
            },
            "objects": {
                "primary": {
                    "id": "U0",
                    "classification": "upstream_qualified_graphiti_serial",
                    "policies": {
                        "deterministic_candidate_ordering": False,
                        "prompt_cache": False,
                        "embedding_cache": False,
                        "caching_counting_embedder": False,
                        "cross_run_cache_carry_over": "prohibited",
                    },
                },
                "guardrail": {
                    "id": "U0-S",
                    "role": "separately_labeled_guardrail_not_primary",
                    "policies": {"deterministic_candidate_ordering": True},
                },
            },
            "runtime_identities": {
                "graphiti": {
                    "version": graphiti_version,
                    "commit": graphiti_commit,
                },
                "construction": {
                    "served_model_id": construction["served_model_name"],
                    "model_revision": CONSTRUCTION_REVISION,
                    "vllm_version": construction["vllm_version"],
                    "max_model_len": construction["max_model_len"],
                    "dtype": construction["dtype"],
                    "quantization": construction["quantization"],
                    "enable_thinking": False,
                },
                "embedding": {
                    "served_model_id": embedding["served_model_id"],
                    "deployment_fingerprint": embedding["identity_value"],
                    "vllm_version": evidence[_EMBEDDING_EVIDENCE][
                        "endpoint_observation"
                    ]["vllm_version"],
                    "dimension": embedding["dimension"],
                    "dtype": embedding["dtype"],
                    "pooling": embedding["pooling"],
                    "normalization": embedding["normalization"],
                    "instruction_policy": embedding["instruction_policy"],
                },
                "neo4j": {
                    "version": "5.26.0",
                    "edition": "community",
                    "deployment": "local_non_docker",
                    "uri": "bolt://localhost:7687",
                },
            },
            "construction_compatibility_policy": {
                "classification": "qwen_vllm_compatibility_adapter",
                "upstream_graphiti_behavior": False,
                "temperature": 0.0,
                "top_p": 1.0,
                "seed": 20260806,
                "enable_thinking": False,
                "requested_max_tokens": 16384,
                "effective_budget_formula": (
                    "max(0,min(requested_max_tokens,"
                    "context_limit-prompt_tokens-safety_margin_tokens))"
                ),
                "safety_margin_tokens": 32,
                "structured_output_mode": "json_schema",
                "episode_indices": [0],
            },
            "screening": {
                "c0": c0_block,
                "e1_e2": {
                    "shared_native_trace": True,
                    "block_order": e1_e2_blocks,
                },
                "e3": {
                    "history_id": first_history["history_id"],
                    "normalized_offered_load_order": [0.5, 0.8, 1.0, 1.2, 1.5],
                    "block_order": e3_blocks,
                },
                "e4": {
                    "history_id": first_history["history_id"],
                    "concurrency_order": [1, 2, 4, 8],
                    "block_order": e4_blocks,
                },
            },
            "input_hashes": source_files,
        }
    )

    phase_specs = (
        ("graphiti_instance", "add_episode", "add-episode"),
        ("graphiti_instance", "retrieve_episodes", "previous-context"),
        ("graphiti_core.graphiti_alias", "extract_nodes", "node-extraction"),
        (
            "graphiti_core.graphiti_alias",
            "resolve_extracted_nodes",
            "node-resolution",
        ),
        ("graphiti_core.graphiti_alias", "extract_edges", "edge-extraction"),
        (
            "graphiti_core.graphiti_alias",
            "resolve_extracted_edges",
            "edge-resolution",
        ),
        (
            "graphiti_core.graphiti_alias",
            "extract_attributes_from_nodes",
            "attributes-summary",
        ),
        ("graphiti_instance", "_process_episode_data", "publication"),
    )
    phase_map = _seal(
        {
            "schema_version": "membind.native-characterization-phase-map.v1",
            "artifact_id": "native-characterization-phase-map",
            "run_id": "native-characterization-freeze",
            "creation_command": (
                ".venv/bin/python src/native_characterization_freeze.py --write"
            ),
            "protocol_id": WORKPLAN_ID,
            "workplan_sha256": WORKPLAN_SHA256,
            "instrumentation_source_sha256": source_files[
                "instrumentation_sha256"
            ],
            "phases": [
                {
                    "owner": owner,
                    "attribute": attribute,
                    "phase": phase,
                    "dependency_class": "unclassified",
                }
                for owner, attribute, phase in phase_specs
            ],
        }
    )
    return freeze, phase_map


def _atomic_write(path: Path, encoded: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_artifacts(
    freeze: Mapping[str, Any],
    phase_map: Mapping[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    """Validate and atomically write exactly the two frozen artifacts."""

    validate_artifact(freeze)
    validate_artifact(phase_map)
    output = Path(output_dir)
    written: dict[str, str] = {}
    for name, payload in (("freeze.json", freeze), ("phase_map.json", phase_map)):
        encoded = canonical_bytes(payload) + b"\n"
        _atomic_write(output / name, encoded)
        written[name] = _sha256_bytes(encoded)
    return written


def _main() -> int:
    validation = Path(__file__).resolve().parents[1]
    repo = validation.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(
            "/data/predator/ly/Mem/data/raw/longmemeval-cleaned/"
            "longmemeval_s_cleaned.json"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=validation / "artifacts/native_characterization",
    )
    args = parser.parse_args()
    freeze, phase_map = build_artifacts(
        repo_root=repo,
        validation_root=validation,
        source_path=args.source,
    )
    if not args.write:
        print(
            json.dumps(
                {
                    "freeze_payload_sha256": freeze["payload_sha256"],
                    "phase_map_payload_sha256": phase_map["payload_sha256"],
                    "write_performed": False,
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    written = write_artifacts(freeze, phase_map, args.output)
    print(
        json.dumps(
            {"written_sha256": written},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
