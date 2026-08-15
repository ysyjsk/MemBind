"""Static safety contract for the detached fixed-three launcher."""

from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
LAUNCHER = PROJECT / "scripts/run_s4_sidecar_qualification_tmux.sh"


def test_launcher_is_detached_explicit_resume_safe_and_secret_free() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")

    assert 'SESSION="membind-pev3-s4-fixed-three-001"' in source
    assert "tmux new-session -d" in source
    assert "PYTHONUNBUFFERED=1" in source
    assert " -u -m paper_eval.s4_sidecar_qualification_controller" in source
    assert "S4_D0_QUALIFICATION_EXECUTION_AUTHORITY_SIDECAR_V1.json" in source
    assert "S4_D0_FIXED_THREE_RESULT_SIDECAR_V1.json" in source
    assert "tee -a" in source
    assert "source " not in source
    assert "api_key" not in source.casefold()
