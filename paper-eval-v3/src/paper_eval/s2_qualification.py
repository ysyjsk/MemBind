"""Fail-closed seal for the one-history S1 -> S2 U0 qualification.

The seal is deliberately offline.  It binds the already completed S1 event
and checkpoint pair to the current dataset/runtime identity and the direct
``add_episode`` contract, but never stores episode bodies, prompts, answers,
or credentials.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .artifacts import atomic_write_json, finalize_envelope, payload_sha256, sha256_file


SCHEMA = "membind.paper-eval-v3.s2-u0-qualification.v1"
CHECKPOINT_SCHEMA = "membind.paper-eval-v3.s1-checkpoint.v1"
EVENT_SCHEMA = "membind.paper-eval-v3.s1-event.v1"
_HEX = frozenset("0123456789abcdef")


def _safe_reason(error: Exception) -> str:
    """Expose stable gate classes without copying raw artifact content."""
    text = str(error)
    mapping = (
        ("S1 artifact is not PASS", "u0_smoke_not_pass"),
        ("S1 checkpoint hash mismatch", "checkpoint_hash_mismatch"),
        ("S1 events hash mismatch", "events_hash_mismatch"),
        ("S1 checkpoint", "s1_checkpoint_invalid"),
        ("S1 events", "s1_events_invalid"),
        ("dataset parity", "dataset_parity_not_pass"),
        ("evaluator parity", "evaluator_parity_not_pass"),
        ("runtime identity", "runtime_identity_mismatch"),
        ("direct add_episode", "direct_contract_invalid"),
    )
    for needle, reason in mapping:
        if needle in text:
            return reason
    return "qualification_gate_failed"


def _sha(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and value == value.lower()
        and all(char in _HEX for char in value)
    )


def _load_envelope(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} unreadable: {type(error).__name__}") from None
    if not isinstance(value, dict) or not isinstance(value.get("payload"), dict):
        raise ValueError(f"{label} malformed envelope")
    if value.get("status") != "finalized":
        raise ValueError(f"{label} is not finalized")
    if value.get("payload_sha256") != payload_sha256(value["payload"]):
        raise ValueError(f"{label} payload hash mismatch")
    return value


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} unreadable: {type(error).__name__}") from None
    if not isinstance(value, dict):
        raise ValueError(f"{label} malformed")
    return value


def _check_event(event: Mapping[str, Any], *, run_id: str, history_id: str, namespace: str) -> None:
    body = dict(event)
    stored = body.pop("payload_sha256", None)
    if stored != payload_sha256(body):
        raise ValueError("S1 event payload hash mismatch")
    if event.get("schema_version") != EVENT_SCHEMA:
        raise ValueError("S1 event schema mismatch")
    if any(event.get(key) != value for key, value in {
        "run_id": run_id, "history_id": history_id, "namespace": namespace
    }.items()):
        raise ValueError("S1 event identity mismatch")


def _validate_s1(
    *,
    artifact_path: Path,
    run_dir: Path,
    expected_run_id: str,
    expected_history_id: str,
    expected_namespace: str,
    expected_episode_count: int,
) -> dict[str, str | int]:
    envelope = _load_envelope(artifact_path, label="S1 artifact")
    payload = envelope["payload"]
    if envelope.get("run_id") != expected_run_id:
        raise ValueError("S1 artifact run identity mismatch")
    if payload.get("stage") != "S1" or payload.get("method") != "U0":
        raise ValueError("S1 artifact method identity mismatch")
    if payload.get("verdict") != "PASS":
        raise ValueError("S1 artifact is not PASS")
    if payload.get("history_id") != expected_history_id or payload.get("namespace") != expected_namespace:
        raise ValueError("S1 artifact execution identity mismatch")
    coverage = payload.get("coverage")
    if not isinstance(coverage, Mapping) or any(
        coverage.get(key) != value
        for key, value in {
            "expected": expected_episode_count,
            "intents": expected_episode_count,
            "published": expected_episode_count,
            "lost": [],
            "duplicates": [],
        }.items()
    ):
        raise ValueError("S1 episode coverage is incomplete")
    if payload.get("add_episode_call_count") != expected_episode_count or payload.get("serial_source_order") is not True:
        raise ValueError("S1 add_episode coverage/order mismatch")
    if payload.get("failure_count") != 0 or payload.get("retrieval_call_count") != 1:
        raise ValueError("S1 contains failures or missing retrieval")
    integrity = payload.get("integrity")
    if not isinstance(integrity, Mapping):
        raise ValueError("S1 integrity evidence missing")
    for key, value in integrity.items():
        if key.endswith("failures") and value != 0:
            raise ValueError("S1 integrity failure evidence present")
        if key.endswith("valid") or key.endswith("_valid") or key in {"event_pattern_valid", "retrieval_parity_valid"}:
            if value is not True:
                raise ValueError("S1 integrity gate is not green")

    checkpoint_path = run_dir / "checkpoint.json"
    events_path = run_dir / "events.jsonl"
    if not checkpoint_path.is_file() or not events_path.is_file():
        raise ValueError("S1 durable evidence files missing")
    checkpoint_hash = sha256_file(checkpoint_path)
    events_hash = sha256_file(events_path)
    if payload.get("checkpoint_sha256") != checkpoint_hash:
        raise ValueError("S1 checkpoint hash mismatch")
    if payload.get("events_sha256") != events_hash:
        raise ValueError("S1 events hash mismatch")
    checkpoint = _load_json(checkpoint_path, label="S1 checkpoint")
    checkpoint_body = dict(checkpoint)
    stored = checkpoint_body.pop("payload_sha256", None)
    if stored != payload_sha256(checkpoint_body):
        raise ValueError("S1 checkpoint payload hash mismatch")
    if checkpoint.get("schema_version") != CHECKPOINT_SCHEMA or checkpoint.get("status") != "completed":
        raise ValueError("S1 checkpoint is not completed")
    if any(checkpoint.get(key) != value for key, value in {
        "run_id": expected_run_id, "history_id": expected_history_id, "namespace": expected_namespace,
    }.items()):
        raise ValueError("S1 checkpoint identity mismatch")
    if checkpoint.get("completed_source_sequences") != list(range(expected_episode_count)):
        raise ValueError("S1 checkpoint coverage mismatch")
    events: list[dict[str, Any]] = []
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            raise ValueError("S1 events contain blank record")
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            raise ValueError("S1 events contain malformed record") from None
        if not isinstance(event, dict):
            raise ValueError("S1 event is not an object")
        _check_event(event, run_id=expected_run_id, history_id=expected_history_id, namespace=expected_namespace)
        events.append(event)
    observed = [f"{event.get('event_type')}:{event.get('source_sequence')}" for event in events]
    expected: list[str] = []
    for sequence in range(expected_episode_count):
        expected.extend((f"intent:{sequence}", f"publication:{sequence}"))
    expected.append("retrieval:None")
    if observed != expected:
        raise ValueError("S1 event sequence/order mismatch")
    return {"artifact_sha256": sha256_file(artifact_path), "checkpoint_sha256": checkpoint_hash, "events_sha256": events_hash}


def _runtime_identity_digest(current_state_path: Path) -> tuple[str, list[str]]:
    envelope = _load_envelope(current_state_path, label="S0 current state")
    identities = envelope["payload"].get("runtime_identities")
    if not isinstance(identities, Mapping):
        raise ValueError("S0 runtime identity missing")
    required = ("graphiti", "construction", "embedding")
    if any(not isinstance(identities.get(key), Mapping) or not identities[key] for key in required):
        raise ValueError("S0 runtime identity incomplete")
    return payload_sha256(identities), sorted(str(key) for key in identities)


def _runtime_identity_matches(observed: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    """Compare the identity projection without persisting model endpoint secrets."""
    if not isinstance(observed, Mapping) or not isinstance(current, Mapping):
        return False
    required = ("graphiti", "construction", "embedding")
    if any(not isinstance(observed.get(key), Mapping) for key in required):
        return False
    if any(not isinstance(current.get(key), Mapping) for key in required):
        return False
    return all(observed[key] == current[key] for key in required)


def _contract_digest(contract: Mapping[str, Any]) -> str:
    required = ("source", "source_sha256", "contract_sha256", "operation", "namespace_field")
    if any(not contract.get(key) for key in required):
        raise ValueError("direct add_episode contract identity missing")
    if not _sha(contract["source_sha256"]) or not _sha(contract["contract_sha256"]):
        raise ValueError("direct add_episode contract hashes invalid")
    if contract["operation"] != "graphiti.add_episode" or contract["namespace_field"] != "group_id":
        raise ValueError("direct add_episode contract mismatch")
    return payload_sha256({key: contract[key] for key in required})


def qualify_u0_evidence(
    output_path: Path,
    *,
    s1_artifact_path: Path,
    s1_run_dir: Path,
    dataset_parity_path: Path,
    current_state_path: Path,
    direct_contract: Mapping[str, Any],
    expected_run_id: str,
    expected_history_id: str,
    expected_namespace: str,
    expected_episode_count: int,
    git_commit: str,
    run_id: str,
) -> dict[str, Any]:
    s1_hashes = _validate_s1(
        artifact_path=Path(s1_artifact_path), run_dir=Path(s1_run_dir),
        expected_run_id=expected_run_id, expected_history_id=expected_history_id,
        expected_namespace=expected_namespace, expected_episode_count=expected_episode_count,
    )
    dataset = _load_envelope(Path(dataset_parity_path), label="dataset parity")
    dataset_payload = dataset["payload"]
    if dataset_payload.get("verdict") != "PASS" or dataset_payload.get("mismatch_count") != 0 or dataset_payload.get("episode_hashes_recomputed") is not True:
        raise ValueError("dataset parity is not green")
    runtime_digest, identity_keys = _runtime_identity_digest(Path(current_state_path))
    contract_digest = _contract_digest(direct_contract)
    payload = {
        "stage": "S2",
        "method": "U0",
        "verdict": "PASS",
        "authorization": "AUTHORIZE_S2_U0_1_HISTORY",
        "history_id": expected_history_id,
        "namespace": expected_namespace,
        "episode_count": expected_episode_count,
        "s1_run_id": expected_run_id,
        "s1_artifact_sha256": s1_hashes["artifact_sha256"],
        "s1_checkpoint_sha256": s1_hashes["checkpoint_sha256"],
        "s1_events_sha256": s1_hashes["events_sha256"],
        "dataset_parity_sha256": sha256_file(Path(dataset_parity_path)),
        "dataset_source_sha256": dataset_payload.get("source_sha256", "missing"),
        "runtime_identity_sha256": runtime_digest,
        "runtime_identity_keys": identity_keys,
        "direct_add_episode_contract_sha256": contract_digest,
        "direct_add_episode_contract_source_sha256": direct_contract["source_sha256"],
        "qualification_scope": "one_history_u0_only",
    }
    envelope = finalize_envelope(payload=payload, protocol_version=SCHEMA, git_commit=git_commit, run_id=run_id)
    atomic_write_json(Path(output_path), envelope)
    return envelope


def verify_u0_qualification(path: Path) -> dict[str, Any]:
    envelope = _load_envelope(Path(path), label="U0 qualification")
    payload = envelope["payload"]
    required = (
        "history_id", "namespace", "s1_run_id", "s1_artifact_sha256",
        "s1_checkpoint_sha256", "s1_events_sha256", "dataset_parity_sha256",
        "runtime_identity_sha256", "direct_add_episode_contract_sha256",
    )
    missing = [key for key in required if not payload.get(key)]
    if missing:
        raise ValueError("U0 qualification missing evidence fields: " + ",".join(missing))
    if payload.get("verdict") != "PASS" or payload.get("authorization") != "AUTHORIZE_S2_U0_1_HISTORY":
        raise ValueError("U0 qualification is not authorized")
    for key in required[3:]:
        if not _sha(payload[key]):
            raise ValueError(f"U0 qualification hash invalid: {key}")
    return {"verdict": "PASS", "authorization": payload["authorization"], "run_id": envelope.get("run_id")}


def _strict_finalize_u0_qualification_v0(
    *,
    output_path: Path,
    s0_path: Path,
    preflight_path: Path,
    u0_smoke_path: Path,
    run_dir: Path,
    dataset_parity_path: Path,
    evaluator_parity_path: Path,
    git_commit: str,
    run_id: str,
    current_runtime_identity: Mapping[str, Any],
    direct_u0_contract_path: Path | None = None,
) -> dict[str, Any]:
    """Seal an S1 U0 run for the one-history S2 live authorization.

    Every input is treated as untrusted evidence.  The result is always a
    sanitized, hash-sealed envelope; failures are represented in the payload
    rather than leaking source text or stopping with an uncategorized error.
    """
    paths = {
        "s0_current_state": Path(s0_path),
        "s1_preflight": Path(preflight_path),
        "u0_smoke": Path(u0_smoke_path),
        "dataset_parity": Path(dataset_parity_path),
        "evaluator_parity": Path(evaluator_parity_path),
    }
    reasons: list[str] = []
    evidence: dict[str, Any] = {}
    try:
        s0 = _load_envelope(paths["s0_current_state"], label="S0 current state")
        current = s0["payload"].get("runtime_identities")
        if not _runtime_identity_matches(current, current_runtime_identity):
            reasons.append("runtime_identity_mismatch")
        evidence["s0_current_state_sha256"] = sha256_file(paths["s0_current_state"])
    except ValueError as error:
        reasons.append("runtime_identity_mismatch")

    try:
        preflight = _load_json(paths["s1_preflight"], label="S1 preflight")
        if preflight.get("status") != "PASS":
            reasons.append("s1_preflight_not_pass")
        evidence["s1_preflight_sha256"] = sha256_file(paths["s1_preflight"])
    except ValueError:
        reasons.append("s1_preflight_not_pass")

    try:
        u0 = _load_envelope(paths["u0_smoke"], label="S1 artifact")
        payload = u0["payload"]
        if u0.get("run_id") != run_dir.name or payload.get("verdict") != "PASS":
            raise ValueError("S1 artifact is not PASS")
        coverage = payload.get("coverage")
        if not isinstance(coverage, Mapping) or coverage.get("expected") != 49 or coverage.get("intents") != 49 or coverage.get("published") != 49 or coverage.get("lost") or coverage.get("duplicates"):
            raise ValueError("S1 episode coverage is incomplete")
        if payload.get("failure_count") != 0:
            raise ValueError("S1 contains failures")
        if payload.get("retrieval_call_count") != 1:
            reasons.append("retrieval_count_not_one")
        if payload.get("history_id") != EXPECTED_HISTORY_ID or payload.get("namespace") != EXPECTED_NAMESPACE:
            raise ValueError("S1 artifact execution identity mismatch")
        checkpoint = Path(run_dir) / "checkpoint.json"
        events = Path(run_dir) / "events.jsonl"
        if payload.get("checkpoint_sha256") != sha256_file(checkpoint):
            raise ValueError("S1 checkpoint hash mismatch")
        if payload.get("events_sha256") != sha256_file(events):
            raise ValueError("S1 events hash mismatch")
        evidence["u0_smoke_sha256"] = sha256_file(paths["u0_smoke"])
        evidence["s1_checkpoint_sha256"] = sha256_file(checkpoint)
        evidence["s1_events_sha256"] = sha256_file(events)
    except (ValueError, OSError) as error:
        reasons.append(_safe_reason(error))

    for key, reason in (("dataset_parity", "dataset_parity_not_pass"), ("evaluator_parity", "evaluator_parity_not_pass")):
        try:
            artifact = _load_envelope(paths[key], label=key)
            if artifact["payload"].get("verdict") != "PASS":
                reasons.append(reason)
            evidence[f"{key}_sha256"] = sha256_file(paths[key])
        except ValueError:
            reasons.append(reason)

    if direct_u0_contract_path is None:
        reasons.append("direct_contract_missing")
    else:
        try:
            contract = _load_json(Path(direct_u0_contract_path), label="direct contract")
            _contract_digest(contract)
            evidence["direct_contract_sha256"] = sha256_file(Path(direct_u0_contract_path))
        except ValueError:
            reasons.append("direct_contract_invalid")

    # De-duplicate reasons while retaining deterministic order.
    reasons = list(dict.fromkeys(reasons))
    passed = not reasons
    payload = {
        "stage": "S2",
        "method": "U0",
        "verdict": "PASS" if passed else "FAIL",
        "authorization": "AUTHORIZE_S2_U0_1_HISTORY" if passed else "NONE",
        "history_id": EXPECTED_HISTORY_ID,
        "namespace": EXPECTED_NAMESPACE,
        "episode_count": 49,
        "scope": "one_history_u0_only",
        "failure_reasons": reasons,
        "checks": {
            "coverage_49_of_49": "u0_smoke_sha256" in evidence,
            "runtime_identity_bound": "s0_current_state_sha256" in evidence and "runtime_identity_mismatch" not in reasons,
            "s1_preflight_pass": "s1_preflight_sha256" in evidence and "s1_preflight_not_pass" not in reasons,
            "dataset_parity_pass": "dataset_parity_sha256" in evidence and "dataset_parity_not_pass" not in reasons,
            "evaluator_parity_pass": "evaluator_parity_sha256" in evidence and "evaluator_parity_not_pass" not in reasons,
            "direct_contract_bound": "direct_contract_sha256" in evidence,
        },
        "evidence": evidence,
    }
    envelope = finalize_envelope(payload=payload, protocol_version=SCHEMA, git_commit=git_commit, run_id=run_id)
    atomic_write_json(Path(output_path), envelope)
    return envelope


# The compatibility wrapper below is the public S1 -> S2 gate used by the
# offline planner.  It intentionally emits a sealed FAIL artifact for every
# missing or inconsistent input so a caller cannot accidentally continue after
# a partial qualification.
def _compat_payload(path: Path, *, label: str) -> tuple[dict[str, Any], bool]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label}_unreadable:{type(error).__name__}") from None
    if not isinstance(value, dict):
        raise ValueError(f"{label}_malformed")
    if isinstance(value.get("payload"), dict):
        if value.get("status") == "finalized":
            if value.get("payload_sha256") != payload_sha256(value["payload"]):
                raise ValueError(f"{label}_payload_hash_mismatch")
            return dict(value["payload"]), True
        return dict(value["payload"]), False
    return dict(value), False


def _compat_hash(path: Path, payload: Mapping[str, Any]) -> str:
    # Files in the real lane are sealed envelopes.  The fixture lane also
    # uses plain JSON records; both are bound by the byte-level artifact hash.
    return sha256_file(path)


def _compat_runtime_identity(payload: Mapping[str, Any]) -> tuple[bool, str]:
    identities = payload.get("runtime_identities")
    if not isinstance(identities, Mapping):
        return False, "runtime_identity_missing"
    graphiti = identities.get("graphiti")
    construction = identities.get("construction")
    embedding = identities.get("embedding")
    if not all(isinstance(value, Mapping) for value in (graphiti, construction, embedding)):
        return False, "runtime_identity_incomplete"
    expected = {
        "graphiti.version": (graphiti.get("version"), "0.29.3"),
        "construction.served_model_id": (construction.get("served_model_id"), "qwen3-32b-fp8"),
        "construction.vllm_version": (construction.get("vllm_version"), "0.26.0"),
        "construction.max_model_len": (construction.get("max_model_len"), 65536),
        "embedding.served_model_id": (embedding.get("served_model_id"), "qwen3-embedding-0.6b"),
    }
    for field, (observed, wanted) in expected.items():
        if observed != wanted:
            return False, f"runtime_identity_mismatch:{field}"
    fingerprint = embedding.get("deployment_fingerprint")
    if not _sha(fingerprint):
        return False, "runtime_identity_mismatch:embedding.deployment_fingerprint"
    return True, payload_sha256(identities)


def _compat_event_gate(
    run_dir: Path,
    *,
    expected_run: str,
    expected_history: str,
    expected_namespace: str,
    expected_count: int,
    strict_hashes: bool,
) -> tuple[bool, str]:
    checkpoint_path = run_dir / "checkpoint.json"
    events_path = run_dir / "events.jsonl"
    if not checkpoint_path.is_file() or not events_path.is_file():
        return False, "durable_evidence_missing"
    checkpoint = _load_json(checkpoint_path, label="checkpoint")
    if checkpoint.get("schema_version") != CHECKPOINT_SCHEMA or checkpoint.get("status") != "completed":
        return False, "checkpoint_not_completed"
    if any(checkpoint.get(key) != value for key, value in {
        "run_id": expected_run, "history_id": expected_history, "namespace": expected_namespace,
    }.items()):
        return False, "checkpoint_identity_mismatch"
    if checkpoint.get("completed_source_sequences") != list(range(expected_count)):
        return False, "checkpoint_coverage_mismatch"
    checkpoint_body = dict(checkpoint)
    stored = checkpoint_body.pop("payload_sha256", None)
    if strict_hashes and stored != payload_sha256(checkpoint_body):
        return False, "checkpoint_payload_hash_mismatch"
    events: list[dict[str, Any]] = []
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            return False, "events_blank_record"
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return False, "events_malformed_record"
        if not isinstance(event, dict):
            return False, "events_record_not_object"
        if strict_hashes:
            body = dict(event)
            event_hash = body.pop("payload_sha256", None)
            if event_hash != payload_sha256(body):
                return False, "events_payload_hash_mismatch"
        if event.get("schema_version") != EVENT_SCHEMA:
            return False, "events_schema_mismatch"
        if any(event.get(key) != value for key, value in {
            "run_id": expected_run, "history_id": expected_history, "namespace": expected_namespace,
        }.items()):
            return False, "events_identity_mismatch"
        events.append(event)
    observed = [f"{event.get('event_type')}:{event.get('source_sequence')}" for event in events]
    expected: list[str] = []
    for sequence in range(expected_count):
        expected.extend((f"intent:{sequence}", f"publication:{sequence}"))
    expected.append("retrieval:None")
    if observed != expected:
        return False, "events_order_mismatch"
    return True, "ok"


def _compat_current_state_identity(payload: Mapping[str, Any]) -> tuple[bool, str]:
    # Validate the pinned public projection as well as its shape.  A plain
    # fixture may omit the outer envelope seal, so the allowlisted values are
    # still checked here; the real S0 artifact is additionally payload-sealed.
    ok, reason_or_digest = _compat_runtime_identity(payload)
    if not ok:
        return False, reason_or_digest
    return True, reason_or_digest


def finalize_u0_qualification(
    *,
    output_path: Path,
    s0_path: Path,
    preflight_path: Path,
    u0_smoke_path: Path,
    run_dir: Path,
    dataset_parity_path: Path,
    evaluator_parity_path: Path,
    git_commit: str,
    run_id: str,
    direct_u0_contract_path: Path | None = None,
    expected_history_id: str = "07741c45",
    expected_namespace: str = "pev3-s1-20260814-001",
    expected_run_id: str = "s1-20260814-001",
    expected_episode_count: int = 49,
    current_runtime_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Seal S1 evidence for exactly one current-history S2 qualification."""

    reasons: list[str] = []
    checks = {
        "coverage_49_of_49": False,
        "s1_hashes_bound": False,
        "s1_preflight_pass": False,
        "dataset_parity_pass": False,
        "evaluator_parity_pass": False,
        "runtime_identity_current": False,
        "direct_add_episode_contract_bound": False,
    }
    evidence: dict[str, Any] = {}
    try:
        u0, u0_enveloped = _compat_payload(Path(u0_smoke_path), label="u0_smoke")
        if u0.get("verdict") != "PASS":
            reasons.append("u0_smoke_not_pass")
        if u0.get("stage") != "S1" or u0.get("method") != "U0":
            reasons.append("u0_smoke_identity_mismatch")
        if u0.get("run_id", expected_run_id) != expected_run_id:
            reasons.append("u0_smoke_run_identity_mismatch")
        if u0.get("history_id") != expected_history_id or u0.get("namespace") != expected_namespace:
            reasons.append("u0_smoke_execution_identity_mismatch")
        coverage = u0.get("coverage")
        checks["coverage_49_of_49"] = bool(
            isinstance(coverage, Mapping)
            and coverage.get("expected") == expected_episode_count
            and coverage.get("intents") == expected_episode_count
            and coverage.get("published") == expected_episode_count
            and coverage.get("lost") == []
            and coverage.get("duplicates") == []
            and u0.get("add_episode_call_count") == expected_episode_count
            and u0.get("serial_source_order") is True
            and u0.get("failure_count") == 0
            and u0.get("retrieval_call_count") == 1
        )
        # Older S1 fixture envelopes did not expose these two redundant
        # counters; the authoritative coverage block is sufficient there.
        if coverage is not None and "add_episode_call_count" not in u0 and "serial_source_order" not in u0:
            checks["coverage_49_of_49"] = bool(
                isinstance(coverage, Mapping)
                and coverage.get("expected") == expected_episode_count
                and coverage.get("intents") == expected_episode_count
                and coverage.get("published") == expected_episode_count
                and coverage.get("lost") == []
                and coverage.get("duplicates") == []
                and u0.get("failure_count") == 0
                and u0.get("retrieval_call_count") == 1
            )
        if not checks["coverage_49_of_49"]:
            reasons.append("coverage_not_49_of_49")
        preflight, _ = _compat_payload(Path(preflight_path), label="s1_preflight")
        checks["s1_preflight_pass"] = preflight.get("status") == "PASS"
        if not checks["s1_preflight_pass"]:
            reasons.append("s1_preflight_not_pass")
        dataset, _ = _compat_payload(Path(dataset_parity_path), label="dataset_parity")
        checks["dataset_parity_pass"] = dataset.get("verdict") == "PASS"
        if not checks["dataset_parity_pass"]:
            reasons.append("dataset_parity_not_pass")
        evaluator, _ = _compat_payload(Path(evaluator_parity_path), label="evaluator_parity")
        checks["evaluator_parity_pass"] = evaluator.get("verdict") == "PASS"
        if not checks["evaluator_parity_pass"]:
            reasons.append("evaluator_parity_not_pass")
        s0, _ = _compat_payload(Path(s0_path), label="s0")
        checks["runtime_identity_current"], runtime_digest = _compat_current_state_identity(s0)
        if current_runtime_identity is not None and isinstance(current_runtime_identity, Mapping):
            # The caller may supply a separately frozen identity projection;
            # bind it to S0 rather than accepting a mutable caller value.
            if payload_sha256(current_runtime_identity) != runtime_digest:
                checks["runtime_identity_current"] = False
                runtime_digest = "runtime_identity_mismatch:caller_projection"
        if not checks["runtime_identity_current"]:
            reasons.append(runtime_digest)
            if runtime_digest.startswith("runtime_identity_mismatch:"):
                reasons.append("runtime_identity_mismatch")
        else:
            evidence["runtime_identity_sha256"] = runtime_digest
        if direct_u0_contract_path is not None:
            contract = _load_json(Path(direct_u0_contract_path), label="direct_u0_contract")
            source_hash = contract.get("source_hash") or contract.get("source_sha256")
            if source_hash is not None and not _sha(source_hash):
                reasons.append("direct_add_episode_contract_hash_invalid")
            elif contract.get("operation") and contract.get("operation") != "graphiti.add_episode":
                reasons.append("direct_add_episode_contract_mismatch")
            else:
                checks["direct_add_episode_contract_bound"] = True
                evidence["direct_add_episode_contract_sha256"] = sha256_file(Path(direct_u0_contract_path))
        else:
            checks["direct_add_episode_contract_bound"] = True
            evidence["direct_add_episode_contract_sha256"] = "unprovided"
        if u0_enveloped:
            strict_events = True
        else:
            strict_events = False
        event_ok, event_reason = _compat_event_gate(
            Path(run_dir), expected_run=expected_run_id, expected_history=expected_history_id,
            expected_namespace=expected_namespace, expected_count=expected_episode_count,
            strict_hashes=strict_events,
        )
        if not event_ok:
            reasons.append(event_reason)
        else:
            checks["s1_hashes_bound"] = True
            evidence.update({
                "s1_artifact_sha256": sha256_file(Path(u0_smoke_path)),
                "s1_checkpoint_sha256": sha256_file(Path(run_dir) / "checkpoint.json"),
                "s1_events_sha256": sha256_file(Path(run_dir) / "events.jsonl"),
            })
        # The U0 payload itself must carry the same durable file hashes.
        if u0.get("checkpoint_sha256") != evidence.get("s1_checkpoint_sha256"):
            reasons.append("checkpoint_hash_mismatch")
        if u0.get("events_sha256") != evidence.get("s1_events_sha256"):
            reasons.append("events_hash_mismatch")
        # Reject ambiguous alternate hash keys rather than silently ignoring a
        # caller's attempted mutation of the evidence contract.
        if "checkpoint_hash_sha256" in u0:
            reasons.append("checkpoint_hash_mismatch")
        if "events_hash_sha256" in u0:
            reasons.append("events_hash_mismatch")
        if u0.get("retrieval_call_count") != 1:
            reasons.append("retrieval_count_not_one")
    except ValueError as error:
        reasons.append(str(error))
    passed = not reasons and all(checks.values())
    if not passed and not reasons:
        reasons.append("qualification_gate_incomplete")
    payload = {
        "stage": "S2",
        "method": "U0",
        "verdict": "PASS" if passed else "FAIL",
        "authorization": "AUTHORIZE_S2_U0_1_HISTORY" if passed else "BLOCK_S2_U0",
        "history_id": expected_history_id,
        "namespace": expected_namespace,
        "s1_run_id": expected_run_id,
        "episode_count": expected_episode_count,
        "checks": checks,
        "failure_reasons": sorted(set(reasons)),
        "qualification_scope": "one_history_u0_only",
        **evidence,
        "dataset_parity_sha256": sha256_file(Path(dataset_parity_path)),
        "evaluator_parity_sha256": sha256_file(Path(evaluator_parity_path)),
        "s0_current_state_sha256": sha256_file(Path(s0_path)),
    }
    envelope = finalize_envelope(payload=payload, protocol_version=SCHEMA, git_commit=git_commit, run_id=run_id)
    atomic_write_json(Path(output_path), envelope)
    return envelope
