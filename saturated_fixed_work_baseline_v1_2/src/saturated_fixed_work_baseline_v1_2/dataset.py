"""Frozen four-history construction and authored Multi-QA identities."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .contracts import EpisodeInput


EXPECTED_EPISODE_COUNTS = {
    "07741c45": 49,
    "b6019101": 49,
    "6071bd76": 46,
    "a2f3aa27": 44,
}
EXPECTED_SOURCE_TOKENS = {
    "07741c45": 104_014,
    "b6019101": 106_914,
    "6071bd76": 105_786,
    "a2f3aa27": 105_977,
}
EXPECTED_TOKENIZER_REVISION = "aa55da1ecc13d006e8b8e4f54579b1ea8c3db2df"
EXPECTED_WORKLOAD_COMPLEXITY_FILE_SHA256 = (
    "aae807f41f3d4f32913a720feb73942c4afb213d2766f968e613d0d14e026621"
)
EXPECTED_WORKLOAD_COMPLEXITY_PAYLOAD_SHA256 = (
    "04d71aa8881666922f6354e238b50080236e92bba7d39c4b3f59200f24a6e625"
)
EXPECTED_TOKENIZER_FILE_SHA256S = {
    "config.json": "e546dacd2c772660270233f5579e9ab923cc2a7ec5ed3c58c27c2bc62cbf5169",
    "tokenizer.json": "aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4",
    "tokenizer_config.json": "d5d09f07b48c3086c508b30d1c9114bd1189145b74e982a265350c923acd8101",
}
WORKLOAD_COMPLEXITY_RELATIVE_PATH = Path(
    "paper-eval-v3/artifacts/paper_eval/membind_v31/V31_WORKLOAD_COMPLEXITY.json"
)
EXPECTED_QA_SOURCE_SHA256 = (
    "a1e3088193eaf6b866fceb62343ebe09beddc8ad0ed57bc70176232f16b3454b"
)
EXPECTED_DATASET_SHA256 = (
    "d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442"
)
RAW_DATASET = Path(
    "/data/predator/ly/Mem/data/raw/longmemeval-cleaned/longmemeval_s_cleaned.json"
)


class DatasetError(ValueError):
    """Frozen data or authored QA provenance drifted."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return _sha256_bytes(payload)


def _role(message: Any) -> str:
    if isinstance(message, Mapping):
        role = str(
            message.get("role")
            or message.get("speaker")
            or message.get("from")
            or "user"
        )
    elif isinstance(message, (list, tuple)) and message:
        role = str(message[0])
    else:
        role = "user"
    role = role.strip().upper()
    return {"HUMAN": "USER", "CUSTOMER": "USER", "AI": "ASSISTANT", "BOT": "ASSISTANT"}.get(role, role or "USER")


def _content(message: Any) -> str:
    if isinstance(message, Mapping):
        for key in ("content", "text", "message", "value"):
            if message.get(key) is not None:
                return str(message[key])
        return json.dumps(message, ensure_ascii=False, sort_keys=True)
    if isinstance(message, (list, tuple)) and len(message) >= 2:
        return str(message[1])
    return str(message)


def _body(session: Any) -> str:
    if isinstance(session, Mapping):
        messages = next(
            (
                session[key]
                for key in ("messages", "turns", "conversation")
                if isinstance(session.get(key), list)
            ),
            [session],
        )
    elif isinstance(session, list):
        messages = session
    else:
        messages = [session]
    return "\n".join(f"[{_role(row)}] {_content(row).strip()}" for row in messages)


def _source_hash(
    history_id: str,
    session_id: str,
    source_sequence: int,
    reference_time: str,
    body: str,
) -> str:
    return _canonical_sha256(
        {
            "question_id": history_id,
            "session_id": session_id,
            "source_sequence": source_sequence,
            "reference_time": reference_time,
            "body": body,
        }
    )


def freeze_source_token_identity(repository_root: Path) -> dict[str, Any]:
    source = repository_root / WORKLOAD_COMPLEXITY_RELATIVE_PATH
    try:
        file_sha256 = _file_sha256(source)
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise DatasetError("SOURCE_TOKEN_ARTIFACT_UNREADABLE") from None
    if file_sha256 != EXPECTED_WORKLOAD_COMPLEXITY_FILE_SHA256:
        raise DatasetError("SOURCE_TOKEN_ARTIFACT_FILE_HASH_MISMATCH")
    if not isinstance(value, Mapping):
        raise DatasetError("SOURCE_TOKEN_ARTIFACT_INVALID")
    candidate = dict(value)
    observed_payload_sha256 = candidate.pop("payload_sha256", None)
    if (
        observed_payload_sha256 != EXPECTED_WORKLOAD_COMPLEXITY_PAYLOAD_SHA256
        or _canonical_sha256(candidate) != observed_payload_sha256
    ):
        raise DatasetError("SOURCE_TOKEN_ARTIFACT_PAYLOAD_MISMATCH")
    histories = value.get("histories")
    totals = value.get("totals")
    source_identity = value.get("source_identity")
    tokenizer = value.get("tokenizer_identity")
    renderer = value.get("renderer_identity")
    if (
        value.get("schema_version")
        != "membind.paper-eval-v3.membind-v31-workload-complexity.v1"
        or value.get("status") != "PASS"
        or tuple(value.get("history_order", ())) != tuple(EXPECTED_EPISODE_COUNTS)
        or not isinstance(histories, Mapping)
        or not isinstance(totals, Mapping)
        or not isinstance(source_identity, Mapping)
        or not isinstance(tokenizer, Mapping)
        or not isinstance(renderer, Mapping)
    ):
        raise DatasetError("SOURCE_TOKEN_ARTIFACT_INVALID")
    observed_counts = {
        history_id: row.get("source_input_token_count")
        for history_id in EXPECTED_EPISODE_COUNTS
        if isinstance((row := histories.get(history_id)), Mapping)
    }
    observed_episodes = {
        history_id: row.get("episode_count")
        for history_id in EXPECTED_EPISODE_COUNTS
        if isinstance((row := histories.get(history_id)), Mapping)
    }
    if (
        observed_counts != EXPECTED_SOURCE_TOKENS
        or observed_episodes != EXPECTED_EPISODE_COUNTS
        or totals.get("source_input_token_count") != sum(EXPECTED_SOURCE_TOKENS.values())
        or totals.get("episode_count") != sum(EXPECTED_EPISODE_COUNTS.values())
    ):
        raise DatasetError("SOURCE_TOKEN_COUNTS_MISMATCH")
    if (
        source_identity.get("source_dataset_sha256") != EXPECTED_DATASET_SHA256
        or tokenizer.get("repository") != "Qwen/Qwen3-32B-FP8"
        or tokenizer.get("revision") != EXPECTED_TOKENIZER_REVISION
        or tokenizer.get("add_special_tokens") is not False
        or tokenizer.get("file_sha256s") != EXPECTED_TOKENIZER_FILE_SHA256S
    ):
        raise DatasetError("SOURCE_TOKEN_PROVENANCE_MISMATCH")
    renderer_path = repository_root / str(renderer.get("path") or "")
    if (
        renderer.get("sha256") != "0dc97963f4e6143b555853d6061967b6e7606d36e0cba66acc70e27ba0a4d163"
        or not renderer_path.is_file()
        or _file_sha256(renderer_path) != renderer.get("sha256")
    ):
        raise DatasetError("SOURCE_TOKEN_RENDERER_MISMATCH")
    return {
        "schema_version": "membind.saturated-fixed-work.source-token-identity.v1",
        "source_artifact_path": str(WORKLOAD_COMPLEXITY_RELATIVE_PATH),
        "source_artifact_file_sha256": file_sha256,
        "source_artifact_payload_sha256": observed_payload_sha256,
        "source_dataset_sha256": source_identity["source_dataset_sha256"],
        "renderer_sha256": renderer["sha256"],
        "tokenizer_repository": tokenizer["repository"],
        "tokenizer_revision": tokenizer["revision"],
        "tokenizer_file_sha256s": dict(tokenizer["file_sha256s"]),
        "add_special_tokens": tokenizer["add_special_tokens"],
        "source_input_tokens": dict(EXPECTED_SOURCE_TOKENS),
        "total_source_input_tokens": sum(EXPECTED_SOURCE_TOKENS.values()),
    }


def freeze_development_dataset(repository_root: Path) -> dict[str, Any]:
    source_token_identity = freeze_source_token_identity(repository_root)
    if _file_sha256(RAW_DATASET) != EXPECTED_DATASET_SHA256:
        raise DatasetError("DATASET_FILE_HASH_MISMATCH")
    records = json.loads(RAW_DATASET.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise DatasetError("DATASET_NOT_LIST")
    by_id = {str(row.get("question_id")): row for row in records if isinstance(row, Mapping)}
    manifests: list[dict[str, Any]] = []
    for history_id, expected_count in EXPECTED_EPISODE_COUNTS.items():
        row = by_id.get(history_id)
        if row is None:
            raise DatasetError("HISTORY_MISSING")
        sessions = row.get("haystack_sessions")
        dates = row.get("haystack_dates")
        session_ids = row.get("haystack_session_ids")
        if not all(isinstance(value, list) for value in (sessions, dates, session_ids)):
            raise DatasetError("HISTORY_FIELDS_INVALID")
        if not len(sessions) == len(dates) == len(session_ids) == expected_count:
            raise DatasetError("EPISODE_COUNT_MISMATCH")
        source_hashes = [
            _source_hash(
                history_id,
                str(session_ids[index]),
                index,
                str(dates[index]),
                _body(sessions[index]),
            )
            for index in range(expected_count)
        ]
        manifest_rows = [
            {
                "history_id": history_id,
                "session_id": str(session_ids[index]),
                "source_sequence": index,
                "source_hash": source_hashes[index],
                "reference_time": str(dates[index]),
            }
            for index in range(expected_count)
        ]
        manifests.append(
            {
                "history_id": history_id,
                "episode_count": expected_count,
                "source_input_token_count": EXPECTED_SOURCE_TOKENS[history_id],
                "source_sequences": list(range(expected_count)),
                "source_hashes": source_hashes,
                "manifest_sha256": _canonical_sha256(manifest_rows),
            }
        )
    return {
        "schema_version": "membind.saturated-fixed-work.dataset.v1",
        "source_path": str(RAW_DATASET),
        "source_file_sha256": _file_sha256(RAW_DATASET),
        "histories": list(EXPECTED_EPISODE_COUNTS),
        "episode_counts": dict(EXPECTED_EPISODE_COUNTS),
        "episode_count": sum(EXPECTED_EPISODE_COUNTS.values()),
        "source_token_identity": source_token_identity,
        "history_manifests": manifests,
        "dataset_manifest_sha256": _canonical_sha256(manifests),
    }


def load_episode_inputs(
    repository_root: Path,
    history_id: str,
    namespace: str,
) -> tuple[EpisodeInput, ...]:
    """Render the frozen source row into the protocol-owned immutable inputs."""

    del repository_root
    if history_id not in EXPECTED_EPISODE_COUNTS:
        raise DatasetError("HISTORY_NOT_FROZEN")
    if not isinstance(namespace, str) or not namespace:
        raise DatasetError("NAMESPACE_INVALID")
    if _file_sha256(RAW_DATASET) != EXPECTED_DATASET_SHA256:
        raise DatasetError("DATASET_FILE_HASH_MISMATCH")
    records = json.loads(RAW_DATASET.read_text(encoding="utf-8"))
    row = next(
        (
            record
            for record in records
            if isinstance(record, Mapping)
            and str(record.get("question_id")) == history_id
        ),
        None,
    )
    if row is None:
        raise DatasetError("HISTORY_MISSING")
    sessions = row.get("haystack_sessions")
    dates = row.get("haystack_dates")
    session_ids = row.get("haystack_session_ids")
    expected_count = EXPECTED_EPISODE_COUNTS[history_id]
    if not all(isinstance(value, list) for value in (sessions, dates, session_ids)):
        raise DatasetError("HISTORY_FIELDS_INVALID")
    if not len(sessions) == len(dates) == len(session_ids) == expected_count:
        raise DatasetError("EPISODE_COUNT_MISMATCH")
    episodes: list[EpisodeInput] = []
    for index in range(expected_count):
        body = _body(sessions[index])
        session_id = str(session_ids[index])
        reference_time = str(dates[index])
        episodes.append(
            EpisodeInput(
                history_id=history_id,
                session_id=session_id,
                source_sequence=index,
                source_hash=_source_hash(
                    history_id,
                    session_id,
                    index,
                    reference_time,
                    body,
                ),
                reference_time=reference_time,
                body=body,
                namespace=namespace,
            )
        )
    return tuple(episodes)


def load_frozen_qa_source_record(
    repository_root: Path, history_id: str
) -> dict[str, Any]:
    """Load one QA context row only after both data and QA identities pass."""

    if history_id not in EXPECTED_EPISODE_COUNTS:
        raise DatasetError("HISTORY_NOT_FROZEN")
    load_and_validate_qa_inventory(repository_root)
    if _file_sha256(RAW_DATASET) != EXPECTED_DATASET_SHA256:
        raise DatasetError("DATASET_FILE_HASH_MISMATCH")
    try:
        records = json.loads(RAW_DATASET.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise DatasetError("DATASET_UNREADABLE") from None
    matches = [
        dict(record)
        for record in records
        if isinstance(record, Mapping)
        and str(record.get("question_id")) == history_id
    ] if isinstance(records, list) else []
    if len(matches) != 1:
        raise DatasetError("HISTORY_MISSING")
    record = matches[0]
    expected = EXPECTED_EPISODE_COUNTS[history_id]
    fields = (
        record.get("haystack_sessions"),
        record.get("haystack_session_ids"),
        record.get("haystack_dates"),
    )
    if any(not isinstance(value, list) for value in fields) or any(
        len(value) != expected for value in fields if isinstance(value, list)
    ):
        raise DatasetError("HISTORY_FIELDS_INVALID")
    return record


def load_and_validate_qa_inventory(repository_root: Path) -> dict[str, Any]:
    inventory_path = (
        repository_root
        / "baseline_reuse_qa_analysis_20260819/expanded/expanded_qa_inventory.json"
    )
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    source_path = repository_root / str(inventory.get("source_path"))
    source = json.loads(source_path.read_text(encoding="utf-8"))
    if _file_sha256(source_path) != EXPECTED_QA_SOURCE_SHA256:
        raise DatasetError("QA_SOURCE_HASH_MISMATCH")
    if inventory.get("source_sha256") != EXPECTED_QA_SOURCE_SHA256:
        raise DatasetError("QA_INVENTORY_SOURCE_HASH_MISMATCH")
    if tuple(inventory.get("history_order", ())) != tuple(EXPECTED_EPISODE_COUNTS):
        raise DatasetError("QA_HISTORY_ORDER_MISMATCH")
    records = source.get("records") if isinstance(source, Mapping) else None
    if not isinstance(records, list):
        raise DatasetError("QA_SOURCE_RECORDS_INVALID")
    source_by_id = {str(row.get("question_id")): row for row in records}
    questions = inventory.get("questions")
    if not isinstance(questions, list) or len(questions) != 16:
        raise DatasetError("QA_COUNT_MISMATCH")
    counts = {history: 0 for history in EXPECTED_EPISODE_COUNTS}
    seen: set[str] = set()
    for question in questions:
        if not isinstance(question, Mapping):
            raise DatasetError("QA_ROW_INVALID")
        question_id = str(question.get("question_id") or "")
        history_id = str(question.get("history_id") or "")
        if not question_id or question_id in seen or history_id not in counts:
            raise DatasetError("QA_IDENTITY_INVALID")
        seen.add(question_id)
        if question.get("qa_pair_id") != question_id or question.get("question_type") != "knowledge-update":
            raise DatasetError("QA_CONTRACT_MISMATCH")
        source_row = source_by_id.get(history_id)
        if source_row is None:
            raise DatasetError("QA_HISTORY_MISSING")
        session_ids = source_row.get("haystack_session_ids")
        sessions = source_row.get("haystack_sessions")
        if not isinstance(session_ids, list) or not isinstance(sessions, list):
            raise DatasetError("QA_PROVENANCE_SOURCE_INVALID")
        by_session = {
            str(session_id): _body(session)
            for session_id, session in zip(session_ids, sessions, strict=True)
        }
        gold_sessions = question.get("gold_session_ids")
        quotes = question.get("gold_evidence_quotes")
        if not isinstance(gold_sessions, list) or not gold_sessions or not isinstance(quotes, list) or not quotes:
            raise DatasetError("QA_PRIVATE_GOLD_INVALID")
        if any(str(session_id) not in by_session for session_id in gold_sessions):
            raise DatasetError("QA_GOLD_SESSION_MISSING")
        combined = "\n".join(by_session[str(session_id)] for session_id in gold_sessions)
        if any(str(quote) not in combined for quote in quotes):
            raise DatasetError("QA_GOLD_QUOTE_MISSING")
        counts[history_id] += 1
    expected_counts = {history: 4 for history in EXPECTED_EPISODE_COUNTS}
    if counts != expected_counts:
        raise DatasetError("QA_BALANCE_MISMATCH")
    return {
        **inventory,
        "question_count": len(questions),
        "questions_per_history": counts,
        "inventory_sha256": _canonical_sha256(questions),
        "inventory_file_sha256": _file_sha256(inventory_path),
    }


__all__ = [
    "EXPECTED_DATASET_SHA256",
    "EXPECTED_EPISODE_COUNTS",
    "DatasetError",
    "freeze_development_dataset",
    "load_episode_inputs",
    "load_frozen_qa_source_record",
    "load_and_validate_qa_inventory",
]
