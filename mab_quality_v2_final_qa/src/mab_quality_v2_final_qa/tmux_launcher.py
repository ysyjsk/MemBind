"""Build a detached tmux launch whose writes stay in the isolated lane."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path


_SESSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,80}$")


@dataclass(frozen=True)
class TmuxLaunch:
    session_name: str
    command: str
    argv: tuple[str, ...]


def build_tmux_launch(
    *,
    session_name: str,
    project_root: Path,
    artifact_root: Path,
    dataset_path: Path,
    run_id: str,
    history_limit: int,
    mode: str = "full",
) -> TmuxLaunch:
    project = Path(project_root).resolve()
    artifacts = Path(artifact_root).resolve()
    owned_root = (project / "artifacts").resolve()
    if not _SESSION.fullmatch(session_name) or not _SESSION.fullmatch(run_id):
        raise ValueError("TMUX_ID_INVALID")
    if owned_root != artifacts and owned_root not in artifacts.parents:
        raise ValueError("ARTIFACT_ROOT_NOT_OWNED")
    if history_limit not in {1, 4} or mode not in {"smoke", "full"}:
        raise ValueError("TMUX_LAUNCH_MODE_INVALID")
    python = project.parent / "membind-validation" / ".venv" / "bin" / "python"
    if not python.is_file():
        # Unit-test layouts need only a deterministic command projection.
        python = Path("python")
    parts = [
        "cd",
        str(project),
        "&&",
        "env",
        "PYTHONUNBUFFERED=1",
        "PYTHONPATH=src:../paper-eval-v3/src:../membind-validation/src",
        str(python),
        "-u",
        "run_mab_quality_v2.py",
        "run-live",
        "--dataset",
        str(Path(dataset_path).resolve()),
        "--artifact-root",
        str(artifacts),
        "--run-id",
        run_id,
        "--history-limit",
        str(history_limit),
        "--mode",
        mode,
    ]
    command = " ".join(
        value if value in {"&&"} or "=" in value and value.startswith("PYTHON") else shlex.quote(value)
        for value in parts
    )
    argv = ("tmux", "new-session", "-d", "-s", session_name, command)
    return TmuxLaunch(session_name, command, argv)


__all__ = ["TmuxLaunch", "build_tmux_launch"]
