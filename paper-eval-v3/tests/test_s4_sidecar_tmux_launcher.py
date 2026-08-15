"""Static safety contracts for detached S4 sidecar retry launchers."""

from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
LAUNCHER = PROJECT / "scripts/run_s4_sidecar_retry_007_tmux.sh"
RETRY_008_LAUNCHER = PROJECT / "scripts/run_s4_sidecar_retry_008_tmux.sh"


def test_retry_007_launcher_is_detached_explicit_and_line_buffered() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")

    assert 'SESSION="membind-pev3-s4-sidecar-007"' in source
    assert "tmux new-session -d" in source
    assert "set -o pipefail" in source
    assert "PYTHONUNBUFFERED=1" in source
    assert " -u -m paper_eval.s4_sidecar_controller" in source
    assert "--authority" in source
    assert "--consumption" in source
    assert "--result" in source
    assert "tee -a" in source
    assert "source " not in source
    assert "api_key" not in source.casefold()


def test_retry_008_launcher_is_fresh_detached_and_never_reuses_retry_007() -> None:
    source = RETRY_008_LAUNCHER.read_text(encoding="utf-8")

    assert 'SESSION="membind-pev3-s4-sidecar-008"' in source
    assert "S4_SIDECAR_SMOKE_AUTHORIZATION_RETRY_008.json" in source
    assert "s4-sidecar-smoke-retry-008" in source
    assert "S4_D0_SIDECAR_SMOKE_RESULT_RETRY_008.json" in source
    assert "tmux new-session -d" in source
    assert "PYTHONUNBUFFERED=1" in source
    assert " -u -m paper_eval.s4_sidecar_controller" in source
    assert "007" not in source
    assert "source " not in source
    assert "api_key" not in source.casefold()
