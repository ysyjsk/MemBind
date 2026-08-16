"""RED tests for the authority-bound M* live entry point and launcher."""

from __future__ import annotations

from pathlib import Path


def test_mstar_controller_exposes_production_entrypoint() -> None:
    from paper_eval.s5_mstar_controller import execute_s5_mstar_production

    assert callable(execute_s5_mstar_production)


def test_mstar_tmux_launcher_exists() -> None:
    launcher = Path(__file__).parents[1] / "scripts" / "run_s5_mstar_tmux.sh"
    assert launcher.is_file()
    assert launcher.stat().st_mode & 0o111
