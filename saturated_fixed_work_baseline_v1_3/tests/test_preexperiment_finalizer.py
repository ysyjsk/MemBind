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

    assert commit == "c62b548d18bbf0da161069be7be86750e977581c"
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
