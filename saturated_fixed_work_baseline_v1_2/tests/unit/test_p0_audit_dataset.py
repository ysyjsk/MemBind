from __future__ import annotations

import ast
from pathlib import Path

import pytest

from saturated_fixed_work_baseline_v1_2.audit import (
    EXPECTED_HEAD,
    AuditError,
    collect_repository_audit,
    validate_repository_identity,
)
from saturated_fixed_work_baseline_v1_2.dataset import (
    EXPECTED_EPISODE_COUNTS,
    EXPECTED_SOURCE_TOKENS,
    EXPECTED_TOKENIZER_REVISION,
    freeze_development_dataset,
    freeze_source_token_identity,
    load_and_validate_qa_inventory,
)


def test_actual_remote_checkout_and_head_are_audited(repository_root: Path) -> None:
    audit = collect_repository_audit(repository_root)
    assert audit["origin_url"] == "git@github.com:ysyjsk/MemBind.git"
    assert audit["head"] == EXPECTED_HEAD
    assert audit["execution_location"] == "REMOTE_EXPERIMENT_HOST"
    assert isinstance(audit["dirty_paths"], list)
    validate_repository_identity(audit)


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("origin_url", "https://example.invalid/MemBind", "REMOTE_ORIGIN_MISMATCH"),
        ("head", "0" * 40, "HEAD_MISMATCH"),
        ("execution_location", "CONTROL_HOST", "CONTROL_HOST_EXECUTION_FORBIDDEN"),
    ],
)
def test_wrong_repository_identity_fails_closed(
    repository_root: Path, field: str, value: str, code: str
) -> None:
    audit = collect_repository_audit(repository_root)
    audit[field] = value
    with pytest.raises(AuditError, match=code):
        validate_repository_identity(audit)


def test_development_dataset_freeze_has_exact_188_episode_manifest(
    repository_root: Path,
) -> None:
    freeze = freeze_development_dataset(repository_root)
    assert freeze["episode_counts"] == EXPECTED_EPISODE_COUNTS
    assert freeze["episode_count"] == 188
    assert tuple(freeze["histories"]) == tuple(EXPECTED_EPISODE_COUNTS)
    for history in freeze["history_manifests"]:
        assert history["source_sequences"] == list(range(history["episode_count"]))
        assert len(history["source_hashes"]) == history["episode_count"]
        assert len(set(history["source_hashes"])) == history["episode_count"]
        assert len(history["manifest_sha256"]) == 64


def test_source_token_identity_is_frozen_to_the_provenanced_qwen_counts(
    repository_root: Path,
) -> None:
    identity = freeze_source_token_identity(repository_root)
    assert EXPECTED_SOURCE_TOKENS == {
        "07741c45": 104_014,
        "b6019101": 106_914,
        "6071bd76": 105_786,
        "a2f3aa27": 105_977,
    }
    assert identity["source_input_tokens"] == EXPECTED_SOURCE_TOKENS
    assert identity["total_source_input_tokens"] == 422_691
    assert identity["tokenizer_revision"] == EXPECTED_TOKENIZER_REVISION
    assert identity["tokenizer_revision"] == (
        "aa55da1ecc13d006e8b8e4f54579b1ea8c3db2df"
    )
    assert identity["add_special_tokens"] is False
    assert identity["source_dataset_sha256"] == (
        "d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442"
    )
    assert identity["source_artifact_file_sha256"] == (
        "aae807f41f3d4f32913a720feb73942c4afb213d2766f968e613d0d14e026621"
    )
    assert identity["source_artifact_payload_sha256"] == (
        "04d71aa8881666922f6354e238b50080236e92bba7d39c4b3f59200f24a6e625"
    )
    assert set(identity["tokenizer_file_sha256s"]) == {
        "config.json",
        "tokenizer.json",
        "tokenizer_config.json",
    }


def test_development_dataset_manifest_embeds_source_token_identity(
    repository_root: Path,
) -> None:
    frozen = freeze_development_dataset(repository_root)
    assert frozen["source_token_identity"]["source_input_tokens"] == (
        EXPECTED_SOURCE_TOKENS
    )
    assert {
        row["history_id"]: row["source_input_token_count"]
        for row in frozen["history_manifests"]
    } == EXPECTED_SOURCE_TOKENS


def test_authored_qa_inventory_is_exact_and_provenanced(repository_root: Path) -> None:
    inventory = load_and_validate_qa_inventory(repository_root)
    assert inventory["claim_scope"] == "BASELINE_REUSE_4_HISTORY_AUTHORED_EXTENSION"
    assert inventory["source_sha256"] == (
        "a1e3088193eaf6b866fceb62343ebe09beddc8ad0ed57bc70176232f16b3454b"
    )
    assert inventory["question_count"] == 16
    assert inventory["questions_per_history"] == {
        history: 4 for history in EXPECTED_EPISODE_COUNTS
    }
    assert {row["question_type"] for row in inventory["questions"]} == {
        "knowledge-update"
    }


def test_new_protocol_has_no_v5_oracle_imports(repository_root: Path) -> None:
    source_root = repository_root / "saturated_fixed_work_baseline_v1_2" / "src"
    forbidden = "paper_eval.membind_v5_oracle"
    violations: list[str] = []
    for path in source_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(name == forbidden or name.startswith(f"{forbidden}.") for name in names):
                violations.append(str(path.relative_to(repository_root)))
    assert violations == []
