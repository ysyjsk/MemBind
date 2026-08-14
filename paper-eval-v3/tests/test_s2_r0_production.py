from __future__ import annotations

from pathlib import Path

from paper_eval.s2_r0_authorization import REQUIRED_BINDINGS
from paper_eval.s2_r0_controller import production_binding_paths


ROOT = Path(__file__).resolve().parents[1]


def test_production_binding_manifest_is_complete_public_and_existing() -> None:
    bindings = production_binding_paths()
    assert set(bindings) == set(REQUIRED_BINDINGS)
    evidence_generated_by_this_suite = {"focused_green", "full_green"}
    assert all(
        path.is_file()
        for name, path in bindings.items()
        if name not in evidence_generated_by_this_suite
    )
    serialized = "\n".join(str(path) for path in bindings.values()).lower()
    assert ".env" not in serialized
    assert "api_key" not in serialized
    assert "password" not in serialized


def test_s2r0_scripts_keep_offline_seal_and_live_action_separate() -> None:
    finalize_script = (ROOT / "scripts/finalize_s2_r0.py").read_text(encoding="utf-8")
    run_script = (ROOT / "scripts/run_s2_r0.py").read_text(encoding="utf-8")
    assert "finalize_s2r0_offline_qualification" in finalize_script
    assert "finalize_s2r0_authorization" in finalize_script
    assert "execute_s2r0_once" not in finalize_script
    assert "execute_s2r0_once" in run_script
    assert "finalize_s2r0_offline_qualification" not in run_script


def test_offline_seal_fixes_the_historical_evidence_anchors() -> None:
    finalize_script = (ROOT / "scripts/finalize_s2_r0.py").read_text(
        encoding="utf-8"
    )
    for expected_sha256 in (
        "0118ce9fbf288633df7405dad0570f1826665b61541b74cab813c3c3aba57f57",
        "287de35d917ec45f43b9107b55b32aae0be4d513c16f06908b5d7b281ec8894e",
        "7a9401f9fcf1d372854bea09dc6bd351c6f7af117463ddfd0bd399c12fabcffc",
        "bd231978613503aabfe895702de3f21f3c24c5ddd166944b6f37375d08f1f61d",
        "9dfaeafedf497992302614230d7afed75bdfb2c578f42a3f2459cf598b3240a0",
        "3797aa87c66e2260fafaa4776863711801e67672e5182a3aa612b1e6b01962ec",
        "a3ee26a87cdebdb23c42e4827f3ac0ab8e7705ef96caeba2b7490e1350b1c848",
    ):
        assert expected_sha256 in finalize_script


def test_s2r0_production_lane_has_no_reader_judge_cleanup_or_stage_transition() -> None:
    files = (
        ROOT / "src/paper_eval/s2_r0_controller.py",
        ROOT / "src/paper_eval/s2_r0_live.py",
        ROOT / "scripts/run_s2_r0.py",
    )
    combined = "\n".join(path.read_text(encoding="utf-8") for path in files)
    for forbidden in (
        "OfficialFactsReader",
        "build_qualified_qwen_judge",
        "delete_by_group_id",
        "STAGE_STATUS.json",
        "CONSTRUCTION_LLM_BASE_URL",
        "EMBEDDING_BASE_URL",
        "load_env_file",
    ):
        assert forbidden not in combined
