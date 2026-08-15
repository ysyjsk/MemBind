"""Static safety contract for the detached S4 smoke launcher."""

from pathlib import Path


def test_s4_launcher_is_detached_single_session_and_unbuffered() -> None:
    project = Path(__file__).resolve().parents[1]
    source = (project / "scripts/run_s4_tmux.sh").read_text(encoding="utf-8")

    assert "tmux new-session -d" in source
    assert "membind-pev3-s4-smoke" in source
    assert "tmux has-session" in source
    assert "PYTHONUNBUFFERED=1" in source
    assert "../membind-validation/.venv/bin/python" in source
    assert "-m paper_eval.s4_controller" in source
    assert "2>&1 | tee" in source
    assert "api-key" not in source.lower()
    assert "rm -" not in source

