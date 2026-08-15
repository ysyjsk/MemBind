"""Static safety contract for the detached retry-007 live launcher."""

from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
LAUNCHER = PROJECT / "scripts/run_s4_sidecar_retry_007_tmux.sh"


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
