"""Service-free TDD for the real S5 A0 production identity materializer."""

from __future__ import annotations

import json
import shutil
from copy import deepcopy
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256, sha256_file
from paper_eval.s5_a0_production_identity_materializer import (
    S5A0MaterializationError,
    S5A0MaterializationPaths,
    materialize_s5_a0_production_identity,
    verify_s5_a0_production_identity_materialization,
    write_s5_a0_production_identity_materialization_exclusive,
)


PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
LEGACY = ROOT / "membind-validation"
NATIVE = PROJECT / "artifacts/paper_eval/native"
DATASET = Path(
    "/data/predator/ly/Mem/data/raw/longmemeval-cleaned/"
    "longmemeval_s_cleaned.json"
)


def _paths(root: Path = ROOT) -> S5A0MaterializationPaths:
    project = root / "paper-eval-v3"
    legacy = root / "membind-validation"
    return S5A0MaterializationPaths(
        native_baseline_freeze=(
            project / "artifacts/paper_eval/native/NATIVE_BASELINE_V2_FREEZE.json"
        ),
        current_stage_pointer=project / "runtime/CURRENT_STAGE_STATUS.json",
        graphiti_semantic_identity=(
            project
            / "artifacts/paper_eval/native/S5_GRAPHITI_SEMANTIC_API_IDENTITY.json"
        ),
        dataset=DATASET if root == ROOT else root / "dataset.json",
        frozen_split=legacy / "artifacts/dataset/frozen_split_v1_3.json",
        dataset_builder=legacy / "src/dataset.py",
        graphiti_native=legacy / "src/graphiti_native.py",
        runtime_factory=legacy / "src/native_characterization_runtime.py",
        scheduler=project / "src/paper_eval/s5_native_method_adapters.py",
        scheduler_test=project / "tests/test_s5_native_method_adapters.py",
        durable_store=project / "src/paper_eval/s5_durable_attempt_store.py",
        durable_store_test=project / "tests/test_s5_durable_attempt_store.py",
    )


def _materialize(paths: S5A0MaterializationPaths | None = None):
    return materialize_s5_a0_production_identity(
        paths=paths or _paths(),
        git_commit="568afb26053a5f8fb133e29f0583eaa524dad1bd",
        run_id="s5-a0-production-identity-20260816-001",
    )


def _copy_inputs(tmp_path: Path) -> S5A0MaterializationPaths:
    source = _paths()
    target = _paths(tmp_path)
    for field in source.__dataclass_fields__:
        source_path = Path(getattr(source, field))
        target_path = Path(getattr(target, field))
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, target_path)
    return target


def _keys(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            found.add(str(key).casefold())
            found.update(_keys(child))
    elif isinstance(value, list | tuple):
        for child in value:
            found.update(_keys(child))
    return found


def test_real_a0_materialization_binds_exact_repo_and_49_source_workload() -> None:
    bundle = _materialize()
    identity = bundle.production_identity
    config = bundle.runtime_config["payload"]
    result = bundle.materialization["payload"]

    expected_sources = tuple(item.source_hash for item in bundle.native_episodes)
    assert len(expected_sources) == 49
    assert len(set(expected_sources)) == 49
    assert [item.source_sequence for item in bundle.native_episodes] == list(range(49))
    assert result["workload"] == {
        "history_id": "07741c45",
        "episode_count": 49,
        "ordered_source_sha256": list(expected_sources),
        "source_manifest_sha256": payload_sha256(
            [
                {"source_sequence": index, "source_sha256": digest}
                for index, digest in enumerate(expected_sources)
            ]
        ),
    }
    assert result["dataset"] == {
        "file_sha256": sha256_file(DATASET),
        "frozen_split_file_sha256": sha256_file(_paths().frozen_split),
        "episode_builder_source_sha256": sha256_file(_paths().dataset_builder),
    }

    assert identity["method"] == "A0"
    assert identity["qualification_status"] == "IDENTITY_ONLY_UNQUALIFIED"
    assert identity["graphiti_native_source_sha256"] == sha256_file(
        _paths().graphiti_native
    )
    assert identity["runtime_factory_source_sha256"] == sha256_file(
        _paths().runtime_factory
    )
    assert identity["scheduler_source_sha256"] == sha256_file(_paths().scheduler)
    assert identity["scheduler_test_source_sha256"] == sha256_file(
        _paths().scheduler_test
    )
    assert identity["durable_store_source_sha256"] == sha256_file(
        _paths().durable_store
    )
    assert identity["durable_store_test_source_sha256"] == sha256_file(
        _paths().durable_store_test
    )
    assert identity["graphiti_semantic_api_sha256"] == (
        "06909217defc448d7dd380f051b6b282fbb9a8a021c337f998c395fc9bb196fa"
    )
    assert identity["runtime_config_sha256"] == bundle.runtime_config["payload_sha256"]

    assert config["construction"]["max_model_len"] == 65536
    assert config["construction"]["requested_max_tokens"] == 16384
    assert config["construction"]["vllm_version"] == "0.26.0"
    assert config["construction"]["rope_parameters"]["factor"] == 2.0
    assert config["embedding"]["served_model_id"] == "qwen3-embedding-0.6b"
    assert config["embedding"]["dimension"] == 1024
    assert config["graphiti"] == {
        "version": "0.29.3",
        "repository_commit": "021d3a57d511f21b10adaf7fa923bd5c1fce5e9d",
    }
    assert config["method_policy"] == {
        "configured_concurrency": 1,
        "scheduler": "FIFO_SINGLE_WORKER",
        "serial_source_order": True,
    }
    assert set(config["authority"].values()) == {False}
    assert set(result["authority"].values()) == {False}
    assert verify_s5_a0_production_identity_materialization(
        materialization=bundle.materialization,
        runtime_config=bundle.runtime_config,
        production_identity=bundle.production_identity,
        paths=_paths(),
    ) == bundle.materialization


def test_materialization_is_deterministic_and_contains_no_private_runtime_data() -> None:
    first = _materialize()
    second = _materialize()
    assert first.runtime_config == second.runtime_config
    assert first.production_identity == second.production_identity
    assert first.materialization == second.materialization

    forbidden = {
        "api_key",
        "authorization",
        "credential",
        "namespace",
        "password",
        "prompt",
        "request",
        "response",
        "secret",
    }
    assert not (_keys(first.runtime_config) & forbidden)
    assert not (_keys(first.production_identity) & forbidden)
    assert not (_keys(first.materialization) & forbidden)


@pytest.mark.parametrize(
    "field",
    [
        "native_baseline_freeze",
        "current_stage_pointer",
        "graphiti_semantic_identity",
        "dataset",
        "frozen_split",
        "dataset_builder",
        "graphiti_native",
        "runtime_factory",
        "scheduler",
        "scheduler_test",
        "durable_store",
        "durable_store_test",
    ],
)
def test_any_materialized_input_drift_fails_closed(
    tmp_path: Path, field: str
) -> None:
    paths = _copy_inputs(tmp_path)
    bundle = _materialize(paths)
    selected = Path(getattr(paths, field))
    selected.write_bytes(selected.read_bytes() + b"\n")

    with pytest.raises(S5A0MaterializationError, match="drift|invalid|mismatch"):
        verify_s5_a0_production_identity_materialization(
            materialization=bundle.materialization,
            runtime_config=bundle.runtime_config,
            production_identity=bundle.production_identity,
            paths=paths,
        )


def test_missing_input_and_tampered_artifact_fail_closed(tmp_path: Path) -> None:
    paths = _copy_inputs(tmp_path)
    paths.dataset_builder.unlink()
    with pytest.raises(S5A0MaterializationError, match="missing"):
        _materialize(paths)

    bundle = _materialize()
    tampered = deepcopy(bundle.materialization)
    tampered["payload"]["workload"]["episode_count"] = 48
    with pytest.raises(S5A0MaterializationError):
        verify_s5_a0_production_identity_materialization(
            materialization=tampered,
            runtime_config=bundle.runtime_config,
            production_identity=bundle.production_identity,
            paths=_paths(),
        )


def test_three_artifacts_are_written_exclusively_and_round_trip(
    tmp_path: Path,
) -> None:
    bundle = _materialize()
    config_path = tmp_path / "A0_RUNTIME_CONFIG.json"
    identity_path = tmp_path / "A0_PRODUCTION_IDENTITY.json"
    result_path = tmp_path / "A0_IDENTITY_MATERIALIZATION.json"
    written = write_s5_a0_production_identity_materialization_exclusive(
        bundle=bundle,
        runtime_config_path=config_path,
        production_identity_path=identity_path,
        materialization_path=result_path,
        paths=_paths(),
    )

    assert written == {
        "runtime_config_file_sha256": sha256_file(config_path),
        "production_identity_file_sha256": sha256_file(identity_path),
        "materialization_file_sha256": sha256_file(result_path),
    }
    assert json.loads(config_path.read_text(encoding="ascii")) == bundle.runtime_config
    assert json.loads(identity_path.read_text(encoding="ascii")) == (
        bundle.production_identity
    )
    assert json.loads(result_path.read_text(encoding="ascii")) == bundle.materialization

    with pytest.raises(FileExistsError):
        write_s5_a0_production_identity_materialization_exclusive(
            bundle=bundle,
            runtime_config_path=config_path,
            production_identity_path=identity_path,
            materialization_path=result_path,
            paths=_paths(),
        )
