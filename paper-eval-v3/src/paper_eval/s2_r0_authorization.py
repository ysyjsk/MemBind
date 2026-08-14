"""Hash-bound offline qualification and one-shot S2-R0 authorization."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from . import PROTOCOL_VERSION
from .artifacts import finalize_envelope, payload_sha256, sha256_file


OFFLINE_SCHEMA = "membind.paper-eval-v3.s2-r0-offline-qualification.v1"
AUTHORIZATION_SCHEMA = "membind.paper-eval-v3.s2-r0-authorization.v1"
CONSUMPTION_SCHEMA = "membind.paper-eval-v3.s2-r0-consumption.v1"
EXPECTED_HISTORY_ID = "07741c45"
EXPECTED_NAMESPACE = "pev3-s1-20260814-001"
EXPECTED_EPISODE_COUNT = 49
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

REQUIRED_BINDINGS = (
    "parent_protocol",
    "amendment",
    "literature_audit",
    "dataset",
    "frozen_split",
    "dataset_builder_source",
    "dataset_parity",
    "s0_current_state",
    "s1_summary",
    "s1_checkpoint",
    "s1_events",
    "u0_qualification",
    "historical_s2_reference",
    "historical_s2_checkpoint",
    "historical_s2_events",
    "historical_s2_adapter_identity",
    "s2_contract_review",
    "artifacts_source",
    "probe_source",
    "contract_source",
    "authorization_source",
    "live_source",
    "controller_source",
    "probe_test",
    "authorization_test",
    "live_test",
    "controller_test",
    "protocol_test",
    "production_test",
    "finalize_script",
    "run_script",
    "graphiti_graphiti",
    "graphiti_search",
    "graphiti_search_config",
    "graphiti_search_utils",
    "graphiti_neo4j_driver",
    "graphiti_neo4j_search_ops",
    "focused_green",
    "full_green",
    "prior_s2r0_authorization",
    "prior_s2r0_consumption",
    "prior_s2r0_failure",
    "s2r0_failure_root_cause",
    "retry_execution_plan",
    "repair_red",
    "repair_targeted_green",
    "repair_focused_green",
    "repair_full_green",
)


def _sha(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} is not a SHA256")
    return value


def _nonempty(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be nonempty")
    return value


def _load_envelope(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is unreadable: {type(error).__name__}") from None
    if (
        not isinstance(value, dict)
        or value.get("status") != "finalized"
        or not isinstance(value.get("payload"), dict)
        or value.get("payload_sha256") != payload_sha256(value["payload"])
    ):
        raise ValueError(f"{label} envelope is invalid")
    return value


def _binding_hashes(binding_paths: Mapping[str, Path]) -> dict[str, str]:
    if set(binding_paths) != set(REQUIRED_BINDINGS):
        raise ValueError("S2-R0 binding set is incomplete")
    hashes = {
        name: sha256_file(Path(binding_paths[name])) for name in REQUIRED_BINDINGS
    }
    if any(value == "missing" for value in hashes.values()):
        raise ValueError("S2-R0 binding file is missing")
    return hashes


def _junit_green(path: Path) -> int:
    try:
        root = ElementTree.parse(path).getroot()
    except (OSError, ElementTree.ParseError) as error:
        raise ValueError("offline regression evidence is unreadable") from None
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    if not suites:
        raise ValueError("offline regression evidence has no test suite")
    tests = sum(int(suite.get("tests", "0")) for suite in suites)
    failures = sum(int(suite.get("failures", "0")) for suite in suites)
    errors = sum(int(suite.get("errors", "0")) for suite in suites)
    skipped = sum(int(suite.get("skipped", "0")) for suite in suites)
    if tests < 1 or failures or errors or skipped:
        raise ValueError("offline regression is not green")
    return tests


def _validate_config(identity: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(identity, Mapping):
        raise ValueError("S2-R0 retrieval config is invalid")
    value = dict(identity)
    episode = value.get("episode_config")
    if (
        value.get("edge_config") is not None
        or value.get("node_config") is not None
        or value.get("community_config") is not None
        or value.get("limit") != 10
        or value.get("reranker_min_score") != 0
        or value.get("candidate_limit") != 20
        or value.get("search_filter") != "EMPTY"
        or value.get("center_node_uuid") is not None
        or value.get("bfs_origin_node_uuids") is not None
        or value.get("query_vector") is not None
        or not isinstance(episode, Mapping)
        or episode.get("search_methods") != ["bm25"]
        or episode.get("reranker") != "reciprocal_rank_fusion"
        or episode.get("sim_min_score") != 0.6
        or episode.get("mmr_lambda") != 0.5
        or episode.get("bfs_max_depth") != 3
    ):
        raise ValueError("S2-R0 retrieval config drift")
    return value


def _validate_retry_lineage(
    lineage: Mapping[str, Any] | None, bindings: Mapping[str, str]
) -> dict[str, Any] | None:
    if lineage is None:
        return None
    value = dict(lineage)
    if (
        value.get("prior_run_id") != "s2r0-20260814-001"
        or value.get("replacement_run_id") != "s2r0-20260814-002"
        or value.get("failure_classification")
        != "HARNESS_QUERY_PARAMETER_NAME_COLLISION"
        or value.get("automatic_retry") is not False
        or value.get("prior_authorization_sha256")
        != bindings["prior_s2r0_authorization"]
        or value.get("prior_consumption_sha256")
        != bindings["prior_s2r0_consumption"]
        or value.get("prior_failure_sha256") != bindings["prior_s2r0_failure"]
    ):
        raise ValueError("S2-R0 replacement lineage drift")
    for field in (
        "prior_authorization_sha256",
        "prior_consumption_sha256",
        "prior_failure_sha256",
    ):
        _sha(value[field], field=field)
    return value


def _write_exclusive(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
    except FileExistsError:
        raise ValueError("S2-R0 authorization already consumed") from None
    try:
        os.write(descriptor, serialized)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def finalize_s2r0_offline_qualification(
    output_path: Path,
    *,
    binding_paths: Mapping[str, Path],
    expected_parent_protocol_sha256: str,
    retrieval_config_identity: Mapping[str, Any],
    dataset_sha256: str,
    frozen_split_sha256: str,
    frozen_corpus_identity_sha256: str,
    ordered_session_ids_sha256: str,
    gold_session_ids_sha256: str,
    episode_names_sha256: str,
    episode_content_hash_sequence_sha256: str,
    gold_session_count: int,
    git_commit: str,
    run_id: str,
    retry_lineage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Seal the complete offline gate without granting live authority."""

    path = Path(output_path)
    if path.exists():
        raise ValueError("S2-R0 offline qualification already exists")
    bindings = _binding_hashes(binding_paths)
    if bindings["parent_protocol"] != _sha(
        expected_parent_protocol_sha256, field="parent protocol"
    ):
        raise ValueError("parent protocol binding drift")
    dataset_hash = _sha(dataset_sha256, field="dataset")
    split_hash = _sha(frozen_split_sha256, field="frozen split")
    if bindings["dataset"] != dataset_hash:
        raise ValueError("dataset binding drift")
    if bindings["frozen_split"] != split_hash:
        raise ValueError("frozen split binding drift")
    if isinstance(gold_session_count, bool) or gold_session_count != 2:
        raise ValueError("frozen gold session count drift")
    focused_tests = _junit_green(Path(binding_paths["focused_green"]))
    full_tests = _junit_green(Path(binding_paths["full_green"]))
    config = _validate_config(retrieval_config_identity)
    replacement = _validate_retry_lineage(retry_lineage, bindings)
    payload = {
        "schema_version": OFFLINE_SCHEMA,
        "stage": "S2-R0-OFFLINE",
        "verdict": "PASS",
        "live_authorized": False,
        "history_id": EXPECTED_HISTORY_ID,
        "namespace": EXPECTED_NAMESPACE,
        "expected_episode_count": EXPECTED_EPISODE_COUNT,
        "dataset_sha256": dataset_hash,
        "frozen_split_sha256": split_hash,
        "frozen_corpus_identity_sha256": _sha(
            frozen_corpus_identity_sha256, field="frozen corpus"
        ),
        "ordered_session_ids_sha256": _sha(
            ordered_session_ids_sha256, field="ordered session IDs"
        ),
        "gold_session_ids_sha256": _sha(
            gold_session_ids_sha256, field="gold session IDs"
        ),
        "episode_names_sha256": _sha(
            episode_names_sha256, field="episode names"
        ),
        "episode_content_hash_sequence_sha256": _sha(
            episode_content_hash_sequence_sha256,
            field="episode content hash sequence",
        ),
        "gold_session_count": gold_session_count,
        "retrieval_config": config,
        "retrieval_config_sha256": payload_sha256(config),
        "binding_sha256": bindings,
        "focused_test_count": focused_tests,
        "full_offline_test_count": full_tests,
        "historical_s2_mutation_count": 0,
        "s3_authorized": False,
    }
    if replacement is not None:
        payload["retry_lineage"] = replacement
    artifact = finalize_envelope(
        payload=payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit=_nonempty(git_commit, field="git_commit"),
        run_id=_nonempty(run_id, field="run_id"),
    )
    from .artifacts import atomic_write_json

    atomic_write_json(path, artifact)
    return artifact


def _verify_qualification(
    qualification_path: Path, binding_paths: Mapping[str, Path]
) -> tuple[dict[str, Any], str]:
    envelope = _load_envelope(Path(qualification_path), label="S2-R0 qualification")
    payload = envelope["payload"]
    if (
        payload.get("schema_version") != OFFLINE_SCHEMA
        or payload.get("verdict") != "PASS"
        or payload.get("live_authorized") is not False
        or payload.get("s3_authorized") is not False
    ):
        raise ValueError("S2-R0 qualification is not PASS")
    current = _binding_hashes(binding_paths)
    if payload.get("binding_sha256") != current:
        raise ValueError("S2-R0 binding drift")
    return envelope, sha256_file(Path(qualification_path))


def finalize_s2r0_authorization(
    output_path: Path,
    *,
    qualification_path: Path,
    binding_paths: Mapping[str, Path],
    expected_output_path: Path,
    consumption_path: Path,
    git_commit: str,
    run_id: str,
) -> dict[str, Any]:
    """Grant exactly one read-only S2-R0 action after the offline seal."""

    path = Path(output_path)
    result_path = Path(expected_output_path)
    consume_path = Path(consumption_path)
    if path.exists():
        raise ValueError("S2-R0 authorization already exists")
    if result_path.exists() or consume_path.exists():
        raise ValueError("S2-R0 run identity was already used")
    qualification, qualification_sha256 = _verify_qualification(
        Path(qualification_path), binding_paths
    )
    payload = {
        "schema_version": AUTHORIZATION_SCHEMA,
        "stage": "S2-R0",
        "authorization": "RUN_S2_R0_EPISODE_BM25_ONCE",
        "history_id": EXPECTED_HISTORY_ID,
        "namespace": EXPECTED_NAMESPACE,
        "run_id": _nonempty(run_id, field="run_id"),
        "expected_output_path": str(result_path.resolve()),
        "consumption_path": str(consume_path.resolve()),
        "qualification_sha256": qualification_sha256,
        "qualification_payload_sha256": qualification["payload_sha256"],
        "binding_sha256": qualification["payload"]["binding_sha256"],
        "dataset_sha256": qualification["payload"]["dataset_sha256"],
        "frozen_split_sha256": qualification["payload"]["frozen_split_sha256"],
        "frozen_corpus_identity_sha256": qualification["payload"][
            "frozen_corpus_identity_sha256"
        ],
        "ordered_session_ids_sha256": qualification["payload"][
            "ordered_session_ids_sha256"
        ],
        "gold_session_ids_sha256": qualification["payload"][
            "gold_session_ids_sha256"
        ],
        "episode_names_sha256": qualification["payload"]["episode_names_sha256"],
        "episode_content_hash_sequence_sha256": qualification["payload"][
            "episode_content_hash_sequence_sha256"
        ],
        "gold_session_count": qualification["payload"]["gold_session_count"],
        "retrieval_config": qualification["payload"]["retrieval_config"],
        "limits": {
            "graphiti_search_calls": 1,
            "construction_llm_requests": 0,
            "embedding_requests": 0,
            "cross_encoder_requests": 0,
            "reader_requests": 0,
            "judge_requests": 0,
            "database_mutation_attempts": 0,
            "namespace_cleanup_calls": 0,
            "retry_count": 0,
        },
        "neo4j_auto_schema_initialization": False,
        "driver_routing_policy": "read_only",
        "raw_content_persistence": False,
        "s3_authorized": False,
    }
    if qualification["payload"].get("retry_lineage") is not None:
        payload["retry_lineage"] = qualification["payload"]["retry_lineage"]
    artifact = finalize_envelope(
        payload=payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit=_nonempty(git_commit, field="git_commit"),
        run_id=run_id,
    )
    from .artifacts import atomic_write_json

    atomic_write_json(path, artifact)
    return artifact


def consume_s2r0_authorization(
    authorization_path: Path,
    consumption_path: Path,
    *,
    binding_paths: Mapping[str, Path],
    expected_run_id: str,
    git_commit: str,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    """Atomically consume the one-shot authority before any live I/O."""

    consume_path = Path(consumption_path)
    if consume_path.exists():
        raise ValueError("S2-R0 authorization already consumed")
    authorization = _load_envelope(
        Path(authorization_path), label="S2-R0 authorization"
    )
    payload = authorization["payload"]
    current = _binding_hashes(binding_paths)
    if payload.get("binding_sha256") != current:
        raise ValueError("S2-R0 binding drift")
    if (
        payload.get("schema_version") != AUTHORIZATION_SCHEMA
        or payload.get("authorization") != "RUN_S2_R0_EPISODE_BM25_ONCE"
        or payload.get("run_id") != expected_run_id
        or payload.get("history_id") != EXPECTED_HISTORY_ID
        or payload.get("namespace") != EXPECTED_NAMESPACE
        or Path(str(payload.get("consumption_path", ""))).resolve()
        != consume_path.resolve()
        or Path(str(payload.get("expected_output_path", ""))).exists()
        or payload.get("s3_authorized") is not False
    ):
        raise ValueError("S2-R0 authorization identity drift")
    authorization_sha256 = sha256_file(Path(authorization_path))
    consumption_payload = {
        "schema_version": CONSUMPTION_SCHEMA,
        "stage": "S2-R0",
        "status": "CONSUMED_BEFORE_LIVE_IO",
        "run_id": expected_run_id,
        "history_id": EXPECTED_HISTORY_ID,
        "namespace": EXPECTED_NAMESPACE,
        "authorization_sha256": authorization_sha256,
        "binding_sha256": current,
        "live_io_performed_at_consumption": False,
        "s3_authorized": False,
    }
    consumed = finalize_envelope(
        payload=consumption_payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit=_nonempty(git_commit, field="git_commit"),
        run_id=expected_run_id,
    )
    _write_exclusive(consume_path, consumed)
    return consumed, authorization_sha256, dict(payload)
