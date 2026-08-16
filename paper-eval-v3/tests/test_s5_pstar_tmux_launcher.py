"""Offline safety tests for the detached S5 P*(C=2) launcher."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from paper_eval.s5_pstar_controller import build_parser


PROJECT = Path(__file__).resolve().parents[1]
LAUNCHER = PROJECT / "scripts/run_s5_pstar_tmux.sh"
RUN_ID = "s5-p-star-20260816-901"
NAMESPACE = f"pev3-{RUN_ID}"
GIT_COMMIT = "5" * 40


def _executable(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _fixture(tmp_path: Path) -> tuple[Path, dict[str, str], Path, Path, Path]:
    authority = tmp_path / "authority.json"
    authority.write_text("{}\n", encoding="utf-8")
    python = _executable(
        tmp_path / "fake-python",
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "if [[ ${FAKE_READER_FAIL:-0} == 1 ]]; then exit 19; fi\n"
        "printf '%s\\t%s\\t%s\\t%s\\n' \"${FAKE_RUN_ID}\" \"${FAKE_NAMESPACE}\" \"${FAKE_GIT_COMMIT}\" \"${FAKE_CONCURRENCY}\"\n",
    )
    tmux_log = tmp_path / "tmux-args.txt"
    tmux = _executable(
        tmp_path / "fake-tmux",
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "if [[ $1 == has-session ]]; then\n"
        "  [[ ${FAKE_HAS_SESSION:-0} == 1 ]] && exit 0\n"
        "  exit 1\n"
        "fi\n"
        "printf '%s\\n' \"$*\" > \"${FAKE_TMUX_LOG}\"\n",
    )
    runs = tmp_path / "runs"
    logs = tmp_path / "logs"
    runs.mkdir()
    logs.mkdir()
    inputs = {
        "S5_PSTAR_PRODUCTION_IDENTITY": tmp_path / "production-identity.json",
        "S5_PSTAR_PRODUCTION_IDENTITY_QUALIFICATION": tmp_path / "qualification.json",
        "S5_PSTAR_PREFLIGHT": tmp_path / "preflight.json",
        "S5_PSTAR_PREDECESSOR": tmp_path / "S5_A0_RESULT.json",
        "S5_PSTAR_CURRENT_STAGE_POINTER": tmp_path / "current-stage.json",
        "S5_PSTAR_ENV_FILE": tmp_path / "private.env",
    }
    for path in inputs.values():
        path.write_text("{}\n", encoding="utf-8")
    env = {
        **os.environ,
        "S5_PSTAR_PYTHON": str(python),
        "S5_PSTAR_TMUX_BIN": str(tmux),
        "S5_PSTAR_RUNS_ROOT": str(runs),
        "S5_PSTAR_LOG_DIR": str(logs),
        "FAKE_RUN_ID": RUN_ID,
        "FAKE_NAMESPACE": NAMESPACE,
        "FAKE_GIT_COMMIT": GIT_COMMIT,
        "FAKE_CONCURRENCY": "2",
        "FAKE_TMUX_LOG": str(tmux_log),
        **{name: str(path) for name, path in inputs.items()},
    }
    return authority, env, runs, logs, tmux_log


def _run(authority: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(LAUNCHER), str(authority)],
        cwd=PROJECT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_launcher_source_has_detached_verified_nonsecret_chain() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")

    assert "verify_s5_live_authority" in source
    assert "jq" not in source
    assert "S5_PSTAR_CONTROLLER_MODULE" in source
    assert "paper_eval.s5_pstar_controller" in source
    assert "S5_PSTAR_POSTPROCESS_MODULE" in source
    assert "paper_eval.s5_pstar_postprocess" in source
    assert "new-session" in source and "-d" in source
    assert "PYTHONUNBUFFERED=1" in source
    assert " -u -m " in source
    assert "tee -a" in source
    assert "&&" in source
    for option in (
        "--production-identity",
        "--production-identity-qualification",
        "--preflight",
        "--authority",
        "--predecessor",
        "--current-stage-pointer",
        "--env-file",
        "--run-root",
        "--git-commit",
    ):
        assert option in source
    lowered = source.casefold()
    assert "api_key" not in lowered
    assert "api-key" not in lowered
    assert "source .env" not in lowered
    assert "print(os.environ" not in lowered
    assert "rm -" not in lowered


def test_launcher_required_options_match_controller_parser() -> None:
    required = {
        option
        for action in build_parser()._actions
        if action.required
        for option in action.option_strings
        if option.startswith("--")
    }
    assert required == {
        "--production-identity",
        "--production-identity-qualification",
        "--preflight",
        "--authority",
        "--predecessor",
        "--run-root",
        "--git-commit",
    }


def test_launcher_derives_identity_and_starts_one_detached_session(
    tmp_path: Path,
) -> None:
    authority, env, runs, _logs, tmux_log = _fixture(tmp_path)
    env["S5_PSTAR_CONTROLLER_MODULE"] = "qualified.custom_pstar_controller"
    env["S5_PSTAR_POSTPROCESS_MODULE"] = "qualified.custom_pstar_postprocess"

    completed = _run(authority, env)

    assert completed.returncode == 0, completed.stderr
    assert f"namespace={NAMESPACE}" not in completed.stdout
    assert NAMESPACE not in completed.stdout.splitlines()
    tmux_args = tmux_log.read_text(encoding="utf-8")
    run_root = runs / RUN_ID
    assert f"new-session -d -s membind-pev3-{RUN_ID}" in tmux_args
    assert "qualified.custom_pstar_controller" in tmux_args
    assert "qualified.custom_pstar_postprocess" in tmux_args
    assert f"--authority '{authority}'" in tmux_args
    assert f"--predecessor '{env['S5_PSTAR_PREDECESSOR']}'" in tmux_args
    assert f"--run-root '{run_root}'" in tmux_args
    assert f"--git-commit '{GIT_COMMIT}'" in tmux_args
    assert "PYTHONUNBUFFERED=1" in tmux_args
    assert " -u -m " in tmux_args
    assert "&&" in tmux_args
    assert "tee -a" in tmux_args


@pytest.mark.parametrize(
    "existing",
    [
        "authority_consumption.json",
        "controller",
        "attempt",
        "attempt/result.json",
        "post_observation.json",
        "S5_PSTAR_RESULT.json",
        "postprocess/checkpoint.json",
    ],
)
def test_launcher_rejects_existing_single_use_output(
    tmp_path: Path, existing: str
) -> None:
    authority, env, runs, _logs, tmux_log = _fixture(tmp_path)
    selected = runs / RUN_ID / existing
    selected.parent.mkdir(parents=True, exist_ok=True)
    if selected.suffix:
        selected.write_text("existing\n", encoding="utf-8")
    else:
        selected.mkdir()

    completed = _run(authority, env)

    assert completed.returncode != 0
    assert "already exists" in completed.stderr
    assert not tmux_log.exists()


def test_launcher_rejects_log_or_symlink_collision(tmp_path: Path) -> None:
    authority, env, runs, logs, tmux_log = _fixture(tmp_path)
    (logs / f"{RUN_ID}.log").write_text("old\n", encoding="utf-8")
    link = runs / RUN_ID / "attempt"
    link.parent.mkdir(parents=True)
    link.symlink_to(tmp_path / "missing-target", target_is_directory=True)

    completed = _run(authority, env)

    assert completed.returncode != 0
    assert "already exists" in completed.stderr
    assert not tmux_log.exists()


def test_launcher_rejects_existing_tmux_session(tmp_path: Path) -> None:
    authority, env, _runs, _logs, tmux_log = _fixture(tmp_path)
    env["FAKE_HAS_SESSION"] = "1"

    completed = _run(authority, env)

    assert completed.returncode != 0
    assert "session already exists" in completed.stderr
    assert not tmux_log.exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("FAKE_RUN_ID", "../escape"),
        ("FAKE_RUN_ID", "s5-a0-20260816-001"),
        ("FAKE_NAMESPACE", "pev3-wrong-run"),
        ("FAKE_CONCURRENCY", "1"),
    ],
)
def test_launcher_rejects_invalid_verified_identity(
    tmp_path: Path, field: str, value: str
) -> None:
    authority, env, _runs, _logs, tmux_log = _fixture(tmp_path)
    env[field] = value

    completed = _run(authority, env)

    assert completed.returncode != 0
    assert "verified P* authority identity is invalid" in completed.stderr
    assert not tmux_log.exists()


def test_launcher_rejects_reader_failure_missing_input_or_shell_control(
    tmp_path: Path,
) -> None:
    authority, env, _runs, _logs, tmux_log = _fixture(tmp_path)
    env["FAKE_READER_FAIL"] = "1"
    assert _run(authority, env).returncode != 0
    assert not tmux_log.exists()

    env.pop("FAKE_READER_FAIL")
    Path(env["S5_PSTAR_PREDECESSOR"]).unlink()
    missing = _run(authority, env)
    assert missing.returncode != 0
    assert "controller input is unavailable" in missing.stderr
    assert not tmux_log.exists()

    Path(env["S5_PSTAR_PREDECESSOR"]).write_text("{}\n", encoding="utf-8")
    env["S5_PSTAR_ENV_FILE"] = str(tmp_path / "bad'path.env")
    Path(env["S5_PSTAR_ENV_FILE"]).write_text("opaque\n", encoding="utf-8")
    controlled = _run(authority, env)
    assert controlled.returncode != 0
    assert "path is invalid" in controlled.stderr
    assert not tmux_log.exists()


def test_launcher_requires_explicit_predecessor_path(tmp_path: Path) -> None:
    authority, env, _runs, _logs, tmux_log = _fixture(tmp_path)
    env.pop("S5_PSTAR_PREDECESSOR")

    completed = _run(authority, env)

    assert completed.returncode != 0
    assert "S5_PSTAR_PREDECESSOR is required" in completed.stderr
    assert not tmux_log.exists()
