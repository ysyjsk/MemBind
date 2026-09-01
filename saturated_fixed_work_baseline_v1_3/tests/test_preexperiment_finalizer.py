from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_finalizer():
    root = Path(__file__).resolve().parents[2]
    path = root / "scripts" / "finalize_preexperiment_state.py"
    spec = importlib.util.spec_from_file_location("membind_preexperiment_finalizer", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_finalizer_reuses_evaluated_commit_after_evidence_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_finalizer()
    monkeypatch.delenv("MEMBIND_EVALUATION_BASE_COMMIT", raising=False)

    commit, source = module._evaluation_base_commit()

    # The evaluated epoch is intentionally re-bound as provenance is
    # materialized for the current HEAD; the resolver must reuse that exact
    # recorded commit rather than silently selecting a newer artifact commit.
    assert commit == module._read(module.EVIDENCE / "CURRENT_STATE.json")["base_code_commit"]
    assert source == "existing_evidence"


def test_finalizer_requires_valid_explicit_commit() -> None:
    module = _load_finalizer()

    with pytest.raises(ValueError, match="invalid --base-code-commit"):
        module._evaluation_base_commit("not-a-commit")


def test_finalizer_explicit_commit_starts_new_epoch() -> None:
    module = _load_finalizer()
    head = module._git_head()

    commit, source = module._evaluation_base_commit(head)

    assert commit == head
    assert source == "cli"


def test_finalizer_rejects_mixed_evidence_epochs() -> None:
    module = _load_finalizer()
    head = module._git_head()

    with pytest.raises(RuntimeError, match="evidence base_code_commit mismatch"):
        module._require_evidence_base_commit(
            "c62b548d18bbf0da161069be7be86750e977581c",
            [
                {"base_code_commit": "c62b548d18bbf0da161069be7be86750e977581c"},
                {"base_code_commit": head},
            ],
        )

    with pytest.raises(RuntimeError, match="missing_fields=1"):
        module._require_evidence_base_commit(
            "c62b548d18bbf0da161069be7be86750e977581c",
            [{"base_code_commit": "c62b548d18bbf0da161069be7be86750e977581c"}, {}],
        )


def test_materialized_evidence_is_ignored_but_source_changes_are_not() -> None:
    module = _load_finalizer()

    assert module._is_materialized_evidence_path(
        "saturated_fixed_work_baseline_v1_3/structured_output_recovery/CURRENT_STATE.json"
    )
    assert module._is_materialized_evidence_path(
        "mab_quality_v2_final_qa/evidence/OFFICIAL_DATASET_PARITY_REPORT.json"
    )
    assert not module._is_materialized_evidence_path(
        "saturated_fixed_work_baseline_v1_3/src/saturated_fixed_work_baseline_v1_3/mab_live_runner.py"
    )
