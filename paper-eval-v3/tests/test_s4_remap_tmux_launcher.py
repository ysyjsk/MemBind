"""Static safety checks for the detached S4 remap retry launcher."""

from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
LAUNCHER = PROJECT / "scripts/run_s4_remap_retry_005_tmux.sh"


def test_launcher_is_detached_single_session_and_uses_sealed_authority() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")

    assert "tmux new-session -d" in source
    assert "membind-pev3-s4-remap-005" in source
    assert "tmux has-session" in source
    assert "S4_REMAP_SMOKE_AUTHORIZATION_RETRY_005.json" in source
    assert "paper_eval.s4_remap_controller" in source
    assert "PYTHONUNBUFFERED=1" in source
    assert "set -o pipefail" in source
    assert "tee -a" in source
    assert "rm " not in source
