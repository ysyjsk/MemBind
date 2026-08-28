from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from saturated_fixed_work_baseline_v1_3.membind_v6_1.identity import (
    implementation_bundle,
)


ROOT = Path(__file__).resolve().parents[2]


def test_implementation_bundle_changes_when_runner_dependency_changes(tmp_path: Path) -> None:
    runner = tmp_path / "runner.py"
    runner.write_text("VALUE = 1\n", encoding="utf-8")
    first = implementation_bundle(runner)
    runner.write_text("VALUE = 2\n", encoding="utf-8")
    second = implementation_bundle(runner)

    assert first["payload_sha256"] != second["payload_sha256"]
    assert first["components"][1]["payload_sha256"] != second["components"][1]["payload_sha256"]


def test_attempt_preparation_failure_is_retained_without_content_or_secrets(
    tmp_path: Path,
) -> None:
    output = tmp_path / "attempt_preparation.json"
    env = {
        **os.environ,
        "MEMBIND_LOCAL_API_KEY": "fixture-secret",
        "NATIVE_LLM_BASE_URL": "http://127.0.0.1:9/v1",
        "PREPARE_LLM_BASE_URL": "http://127.0.0.1:10/v1",
        "CONSTRUCTION_LLM_MODEL": "fixture-model",
        "CONSTRUCTION_SEED": "1",
    }
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/local_runtime_8b_dual/prepare_measured_attempt.py"),
            "--attempt-id",
            "fixture-attempt",
            "--output",
            str(output),
        ],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["status"] == "FAILED"
    assert evidence["attempt_id"] == "fixture-attempt"
    assert evidence["content_and_secrets_omitted"] is True
    assert "fixture-secret" not in output.read_text(encoding="utf-8")


def test_8b_startup_recovers_neo4j_before_loading_models() -> None:
    startup = (ROOT / "scripts/local_runtime_8b_dual/start_all.sh").read_text(
        encoding="utf-8"
    )
    startup_body = startup[startup.index("# Recover and validate the existing database") :]
    assert startup_body.index('"$SCRIPT_DIR/start_neo4j.sh"') < startup_body.index(
        '"$SCRIPT_DIR/start_native_llm.sh"'
    )
    neo4j = (ROOT / "scripts/local_runtime_8b_dual/start_neo4j.sh").read_text(
        encoding="utf-8"
    )
    assert "neo4j-community-5.26.0" in neo4j
    assert "RETURN 1 AS value" in neo4j
    assert "foreign listener" in neo4j
    assert "nohup setsid" in neo4j


def test_8b_runner_starts_audited_attempt_before_cache_preparation() -> None:
    runner = (
        ROOT / "saturated_fixed_work_baseline_v1_3/scripts/run_mab_v61_8b.py"
    ).read_text(encoding="utf-8")
    attempt_write = runner.index('_write_new(attempt_root / "attempt.json", start)')
    preparation_run = runner.index("subprocess.run(", attempt_write)
    assert attempt_write < preparation_run
    assert 'failure_phase = "ATTEMPT_PREPARATION"' in runner
    assert '"failure_phase": failure_phase' in runner


def test_8b_status_rejects_a_stale_platform_declaration() -> None:
    status = (ROOT / "scripts/local_runtime_8b_dual/status.sh").read_text(
        encoding="utf-8"
    )
    assert "declared_payload_sha256" in status
    assert "latest_payload_sha256" in status
    assert "STALE_PLATFORM_DECLARATION" in status


def test_8b_activation_declares_all_experiment_source_roots() -> None:
    activation = (ROOT / "scripts/local_runtime_8b_dual/activate.sh").read_text(
        encoding="utf-8"
    )
    for project in (
        "mab_quality_v2_final_qa/src",
        "saturated_fixed_work_baseline_v1_3/src",
        "paper-eval-v3/src",
    ):
        assert project in activation
    assert "Do not expose membind-validation/src globally" in activation
    for launcher in (
        "start_native_llm.sh",
        "start_prepare_llm.sh",
        "start_embedding.sh",
    ):
        source = (ROOT / "scripts/local_runtime_8b_dual" / launcher).read_text(
            encoding="utf-8"
        )
        assert "env -u PYTHONPATH" in source
