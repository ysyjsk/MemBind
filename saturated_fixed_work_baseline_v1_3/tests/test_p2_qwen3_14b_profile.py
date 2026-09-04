from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "scripts" / "local_runtime_qwen3_14b_dual"


def _profile_environment(data_root: Path) -> dict[str, str]:
    command = f"source {PROFILE / 'local_env.sh'}; env"
    environment = dict(os.environ)
    environment["MEMBIND_8B_DATA_ROOT"] = str(data_root)
    result = subprocess.run(
        ["bash", "-c", command],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def test_p2_profile_environment_is_isolated_and_native_context(tmp_path: Path) -> None:
    environment = _profile_environment(tmp_path)

    assert environment["MEMBIND_DEPLOYMENT_POLICY_ID"] == "P2_QWEN3_14B_AWQ"
    assert environment["MEMBIND_PROFILE_ID"] == "local-qwen3-14b-awq-dualreplica-v1"
    assert environment["MEMBIND_LLM_SOURCE_MODEL"] == "Qwen/Qwen3-14B-AWQ"
    assert environment["MEMBIND_LLM_MODEL_NAME"] == "qwen3-14b-awq"
    assert environment["MEMBIND_LLM_MODEL_REVISION"] == (
        "31c69efc29464b6bb0aee1398b5a7b50a99340c3"
    )
    assert environment["MEMBIND_LLM_MAX_MODEL_LEN"] == "40960"
    assert environment["MEMBIND_PREPARE_LLM_GPU_MEMORY_UTILIZATION"] == "0.72"
    assert environment["MEMBIND_EMBED_GPU_MEMORY_UTILIZATION"] == "0.25"
    assert environment["MEMBIND_GPU1_MAX_COMBINED_UTILIZATION"] == "0.97"
    assert environment["MEMBIND_CONSTRUCTION_TEMPERATURE"] == "0.7"
    assert environment["MEMBIND_CONSTRUCTION_TOP_P"] == "0.8"
    assert environment["MEMBIND_CONSTRUCTION_TOP_K"] == "20"
    assert environment["MEMBIND_CONSTRUCTION_ENABLE_THINKING"] == "false"
    assert "MEMBIND_CONSTRUCTION_REPETITION_PENALTY" not in environment
    assert environment["MEMBIND_CONSTRUCTION_MIN_P"] == "0"
    assert environment["MEMBIND_CONSTRUCTION_PRESENCE_PENALTY"] == "1.5"
    assert "MEMBIND_LLM_HF_OVERRIDES" not in environment
    assert environment["MEMBIND_NATIVE_LLM_TMUX_SESSION"] == "membind-qwen3-14b-native"
    assert environment["MEMBIND_PREPARE_LLM_TMUX_SESSION"] == "membind-qwen3-14b-prepare"
    assert environment["MEMBIND_EMBED_TMUX_SESSION"] == "membind-qwen3-14b-embedding"


def test_p2_dry_run_uses_native_context_without_8b_yarn(tmp_path: Path) -> None:
    model = tmp_path / "models" / "Qwen3-14B-AWQ"
    model.mkdir(parents=True)
    (model / "config.json").write_text("{}\n", encoding="utf-8")
    (model / "model-00001-of-00002.safetensors").write_bytes(b"")
    environment = dict(os.environ)
    environment["MEMBIND_8B_DATA_ROOT"] = str(tmp_path)

    for script in ("start_native_llm.sh", "start_prepare_llm.sh"):
        result = subprocess.run(
            [str(PROFILE / script), "--dry-run"],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        assert "Qwen3-14B-AWQ" in result.stdout
        assert "--served-model-name qwen3-14b-awq" in result.stdout
        assert "--max-model-len 40960" in result.stdout
        assert "--default-chat-template-kwargs" in result.stdout
        assert "enable_thinking" in result.stdout
        assert "--hf-overrides" not in result.stdout


def test_p2_routes_bind_the_new_profile_and_model() -> None:
    routes = sorted((PROFILE / "routing").glob("*.json"))
    assert {path.name for path in routes} == {
        "native_dual_resource_matched.json",
        "native_dual_static_role.json",
        "single_gpu_ablation.json",
        "v61_dual_elastic_affinity.json",
    }
    for path in routes:
        value = json.loads(path.read_text(encoding="utf-8"))
        assert value["profile_id"] == "local-qwen3-14b-awq-dualreplica-v1"
        assert all(
            endpoint["served_model"] == "qwen3-14b-awq"
            for endpoint in value["endpoint_set"]
        )


def test_p2_model_manifest_writer_seals_external_complete_catalog(
    tmp_path: Path,
) -> None:
    model = tmp_path / "model"
    model.mkdir()
    for name, content in {
        "config.json": b'\n{"max_position_embeddings": 40960}\n',
        "generation_config.json": b'{"temperature":0.6,"top_p":0.95,"top_k":20}\n',
        "tokenizer.json": b"{}\n",
        "model-00001-of-00002.safetensors": b"first",
        "model-00002-of-00002.safetensors": b"second",
    }.items():
        (model / name).write_bytes(content)
    output = tmp_path / "profile" / "model_snapshot_manifest.json"

    subprocess.run(
        [
            sys.executable,
            str(PROFILE / "write_model_manifest.py"),
            "--model-root",
            str(model),
            "--output",
            str(output),
        ],
        check=True,
    )
    value = json.loads(output.read_text(encoding="utf-8"))

    assert value["schema_version"] == "membind.model-snapshot-manifest.v2"
    assert value["source_model"] == "Qwen/Qwen3-14B-AWQ"
    assert value["revision"] == "31c69efc29464b6bb0aee1398b5a7b50a99340c3"
    assert value["path"] == str(model.resolve())
    assert value["weight_file_count"] == 2
    assert value["file_count"] == 5
    assert len(value["payload_sha256"]) == 64
