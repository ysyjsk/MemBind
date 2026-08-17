"""TDD contract for the isolated MemBind-v1 aligned-table command."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "scripts/run_membind_v1.py"
HISTORIES = ("07741c45", "b6019101", "6071bd76", "a2f3aa27")


def _payload_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _module():
    spec = importlib.util.spec_from_file_location("run_membind_v1", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _runtime_identity() -> dict[str, object]:
    return {
        "schema_version": "membind.paper-eval-v3.membind-v1-live-runtime.v1",
        "construction": {
            "base_url": "http://public.invalid/v1",
            "served_model_id": "qwen3-32b-fp8",
            "requested_max_tokens": 16384,
            "structured_output_mode": "json_schema",
        },
        "embedding": {
            "base_url": "http://public.invalid/embedding/v1",
            "served_model_id": "qwen3-embedding-0.6b",
            "dimension": 1024,
        },
        "neo4j": {"uri": "bolt://localhost:7687"},
        "graphiti_max_coroutines": 8,
        "global_llm_admission_k": 2,
    }


@dataclass(frozen=True)
class _Episode:
    source_sequence: int
    source_hash: str
    session_id: str
    body: str
    reference_time: str = "2026-01-01T00:00:00+00:00"
    group_id: str = "unscoped"

    @property
    def name(self) -> str:
        return f"episode::{self.source_sequence:04d}"


class _World:
    def __init__(self) -> None:
        self.executed: list[int] = []
        self.measured: list[int] = []
        self.smoke_executed = 0
        self.smoke_verified = 0
        self.fail_at: int | None = None
        self.fail_smoke = False
        self.quality_runtime_closed = 0

    def load_env(self) -> dict[str, str]:
        return {"public": "test"}

    def load_workload(self) -> dict[str, dict[str, object]]:
        return {
            history: {
                "record": {"history_id": history},
                "episodes": tuple(
                    _Episode(
                        source_sequence=index,
                        source_hash=f"{history[:4]}{index + 1:060x}",
                        session_id=f"{history}-session-{index}",
                        body=f"private {history} {index}",
                    )
                    for index in range(2)
                ),
            }
            for history in HISTORIES
        }

    def project_runtime_identity(self, _env: dict[str, str]) -> dict[str, object]:
        return _runtime_identity()

    def implementation_hashes(self) -> dict[str, str]:
        return {
            "aligned_live": "1" * 64,
            "graphiti_adapter": "2" * 64,
            "graphiti_factories": "3" * 64,
            "semantic_trace_binding": "4" * 64,
        }

    def bind_historical(self) -> dict[str, object]:
        return {"schema_version": "historical", "payload_sha256": "a" * 64}

    def build_quality_runtime(self, _env: dict[str, str]) -> object:
        return SimpleNamespace()

    async def close_quality_runtime(self, _runtime: object) -> None:
        self.quality_runtime_closed += 1

    async def execute_smoke(self, **kwargs: object) -> dict[str, object]:
        self.smoke_executed += 1
        if self.fail_smoke:
            raise ConnectionError("smoke upstream unavailable")
        root = kwargs["smoke_root"]
        assert isinstance(root, Path)
        root.mkdir(parents=True, exist_ok=False)
        body = {
            "schema_version": "fake-smoke-result.v1",
            "status": "PASS",
            "formal_plan_payload_sha256": kwargs["plan"]["payload_sha256"],
            "execution_identity_sha256": kwargs["execution_identity_sha256"],
            "membind_artifact_identity_sha256": hashlib.sha256(
                json.dumps(
                    {
                        "operation_identity_sha256": "1" * 64,
                        "model_identity_sha256": "2" * 64,
                        "prompt_identity_sha256": "3" * 64,
                        "schema_identity_sha256": "4" * 64,
                        "config_identity_sha256": "5" * 64,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
            "history_id": "07741c45",
            "method": "MemBind-v1 node-only",
            "source_count": 3,
            "global_llm_admission_k": 2,
        }
        return {**body, "payload_sha256": _payload_sha256(body)}

    def verify_smoke(self, **kwargs: object) -> dict[str, object]:
        self.smoke_verified += 1
        root = kwargs["smoke_root"]
        assert isinstance(root, Path)
        assert root.exists()
        body = {
            "schema_version": "fake-smoke-result.v1",
            "status": "PASS",
            "formal_plan_payload_sha256": kwargs["plan"]["payload_sha256"],
            "execution_identity_sha256": kwargs["execution_identity_sha256"],
            "membind_artifact_identity_sha256": hashlib.sha256(
                json.dumps(
                    {
                        "operation_identity_sha256": "1" * 64,
                        "model_identity_sha256": "2" * 64,
                        "prompt_identity_sha256": "3" * 64,
                        "schema_identity_sha256": "4" * 64,
                        "config_identity_sha256": "5" * 64,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
            "history_id": "07741c45",
            "method": "MemBind-v1 node-only",
            "source_count": 3,
            "global_llm_admission_k": 2,
        }
        return {**body, "payload_sha256": _payload_sha256(body)}

    async def execute_block(self, **kwargs: object) -> dict[str, object]:
        block = kwargs["block"]
        assert isinstance(block, dict)
        index = int(block["block_index"])
        if self.fail_at == index:
            raise ConnectionError("private upstream message")
        self.executed.append(index)
        root = kwargs["block_root"]
        assert isinstance(root, Path)
        root.mkdir(parents=True, exist_ok=False)
        return {"status": "PASS", "block_index": index}

    async def measure_quality(self, **kwargs: object) -> dict[str, object]:
        block = kwargs["block"]
        plan = kwargs["plan"]
        assert isinstance(block, dict)
        assert isinstance(plan, dict)
        index = int(block["block_index"])
        self.measured.append(index)
        quality = {
            "schema_version": (
                "membind.paper-eval-v3.membind-v1-"
                "aligned-quality-correctness.v1"
            ),
            "aligned_run_id": block["aligned_run_id"],
            "block_index": index,
            "method": block["method"],
            "history_id": block["history_id"],
            "plan_payload_sha256": plan["payload_sha256"],
            "manifest_sha256": "b" * 64,
            "execution_identity_sha256": "c" * 64,
            "qa_accuracy": None,
            "evidence_recall_at_10": 0.0,
            "direct_violations": 0,
            "quality_status": "NQ_GRAPH_NATIVE_PROTOCOL_DEGENERATE",
        }
        quality["quality_and_correctness_sha256"] = _payload_sha256(quality)
        return {
            "schema_version": "fake-aligned-quality-live.v1",
            "quality_and_correctness": quality,
        }

    def derive_block(self, **kwargs: object) -> dict[str, object]:
        block = kwargs["block"]
        assert isinstance(block, dict)
        index = int(block["block_index"])
        return {
            "status": "PASS",
            "block_index": index,
            "public_row": {"test_block_index": index},
            "freshness_record": {"test_block_index": index},
        }

    def verify_block(self, **kwargs: object) -> dict[str, object]:
        root = kwargs["block_root"]
        assert isinstance(root, Path)
        return json.loads((root / "block_output.json").read_text(encoding="utf-8"))["derived"]

    def reduce_blocks(self, **kwargs: object) -> list[dict[str, object]]:
        rows = kwargs["public_rows"]
        assert isinstance(rows, list) and len(rows) == 12
        return [
            {
                "method": method,
                "execution_status": "COMPLETED",
                "validity_status": "VALID",
                "quality_status": "NQ_GRAPH_NATIVE_PROTOCOL_DEGENERATE",
                "aligned_run_id": "aligned-main-test-001",
                "arrival_trace_sha256": "1" * 64,
                "source_manifest_sha256": "2" * 64,
                "shared_execution_envelope_sha256": "3" * 64,
                "global_llm_admission_k": 2,
                "metrics": {
                    "qa_accuracy": 0.0,
                    "evidence_recall_at_10": 1.0,
                    "direct_violations": 0,
                    "p95_arrival_to_publication_ns": 1,
                    "p99_arrival_to_publication_ns": 1,
                    "successful_goodput_episodes_per_second": 1.0,
                    "makespan_ns": 1,
                    "max_backlog": 1,
                },
            }
            for method in ("U0-aligned", "P(C=2)-aligned", "MemBind-v1 node-only")
        ]

    def build_table(self, **kwargs: object) -> dict[str, object]:
        assert kwargs["main_table_run_id"] == "main-table-main-test-001"
        return {"status": "PASS", "payload_sha256": "f" * 64, "rows": kwargs["aligned_rows"]}

    def render_table(self, _table: dict[str, object]) -> str:
        return "# Fake Aligned Main Table\n"

    def hooks(self, module):
        return module.Hooks(
            load_env=self.load_env,
            load_workload=self.load_workload,
            project_runtime_identity=self.project_runtime_identity,
            implementation_hashes=self.implementation_hashes,
            bind_historical=self.bind_historical,
            execute_smoke=self.execute_smoke,
            verify_smoke=self.verify_smoke,
            build_quality_runtime=self.build_quality_runtime,
            close_quality_runtime=self.close_quality_runtime,
            execute_block=self.execute_block,
            measure_quality=self.measure_quality,
            derive_block=self.derive_block,
            verify_block=self.verify_block,
            reduce_blocks=self.reduce_blocks,
            build_table=self.build_table,
            render_table=self.render_table,
        )


def test_command_runs_and_seals_all_twelve_blocks_before_the_first_main_table(tmp_path: Path) -> None:
    module = _module()
    world = _World()

    result = module.run_aligned_main_table(
        aligned_run_id="aligned-main-test-001",
        main_table_run_id="main-table-main-test-001",
        membind_runs_root=tmp_path / "membind-runs",
        aligned_table_runs_root=tmp_path / "aligned-runs",
        hooks=world.hooks(module),
    )

    assert result["status"] == "PASS"
    assert world.smoke_executed == 1
    assert world.smoke_verified == 0
    assert world.executed == list(range(12))
    assert world.measured == list(range(12))
    assert world.quality_runtime_closed == 1
    root = tmp_path / "aligned-runs/aligned-main-test-001"
    plan = json.loads((root / "ALIGNED_PLAN.json").read_text(encoding="utf-8"))
    assert plan["interarrival_ns"] == module.FROZEN_INTERARRIVAL_NS
    assert (root / "ALIGNED_MAIN_TABLE.json").exists()
    assert (root / "ALIGNED_MAIN_TABLE.md").read_text(encoding="utf-8") == "# Fake Aligned Main Table\n"


def test_command_reuses_only_verified_complete_block_outputs(tmp_path: Path) -> None:
    module = _module()
    first = _World()
    kwargs = {
        "aligned_run_id": "aligned-main-test-001",
        "main_table_run_id": "main-table-main-test-001",
        "membind_runs_root": tmp_path / "membind-runs",
        "aligned_table_runs_root": tmp_path / "aligned-runs",
    }
    module.run_aligned_main_table(**kwargs, hooks=first.hooks(module))
    second = _World()
    module.run_aligned_main_table(**kwargs, hooks=second.hooks(module))

    assert second.smoke_executed == 0
    assert second.smoke_verified == 1
    assert second.executed == []
    assert second.measured == []
    assert second.quality_runtime_closed == 1


def test_command_stops_at_the_first_failed_block_and_does_not_render_a_partial_table(
    tmp_path: Path,
) -> None:
    module = _module()
    world = _World()
    world.fail_at = 4

    with pytest.raises(ConnectionError):
        module.run_aligned_main_table(
            aligned_run_id="aligned-main-test-001",
            main_table_run_id="main-table-main-test-001",
            membind_runs_root=tmp_path / "membind-runs",
            aligned_table_runs_root=tmp_path / "aligned-runs",
            hooks=world.hooks(module),
        )

    assert world.executed == [0, 1, 2, 3]
    assert world.measured == [0, 1, 2, 3]
    root = tmp_path / "aligned-runs/aligned-main-test-001"
    progress = json.loads((root / "progress.json").read_text(encoding="utf-8"))
    assert progress["status"] == "FAILED_STOPPED"
    assert progress["failed_block_index"] == 4
    assert "private upstream message" not in json.dumps(progress)
    assert not (root / "ALIGNED_MAIN_TABLE.json").exists()


def test_command_stops_before_quality_or_blocks_when_smoke_fails(tmp_path: Path) -> None:
    module = _module()
    world = _World()
    world.fail_smoke = True

    with pytest.raises(ConnectionError, match="smoke upstream unavailable"):
        module.run_aligned_main_table(
            aligned_run_id="aligned-main-test-001",
            main_table_run_id="main-table-main-test-001",
            membind_runs_root=tmp_path / "membind-runs",
            aligned_table_runs_root=tmp_path / "aligned-runs",
            hooks=world.hooks(module),
        )

    assert world.executed == []
    assert world.measured == []
    assert world.quality_runtime_closed == 0
    root = tmp_path / "aligned-runs/aligned-main-test-001"
    progress = json.loads((root / "progress.json").read_text(encoding="utf-8"))
    assert progress["status"] == "SMOKE_FAILED_STOPPED"
    assert progress["failed_block_index"] is None
    assert not (root / "ALIGNED_MAIN_TABLE.json").exists()


def test_command_stays_in_its_new_lane_and_has_no_secret_or_historical_write_path() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "aligned_main_table/runs" in source
    assert "membind_v1/runs" in source
    assert "api_key" not in source.casefold()
    assert "10.87.5.247" not in source


def test_persist_block_output_rejects_missing_quality_seal_with_runner_error(tmp_path: Path) -> None:
    module = _module()
    with pytest.raises(module.RunnerError, match="quality projection seal"):
        module._persist_block_output(
            tmp_path / "block",
            block={
                "aligned_run_id": "aligned-main-test-001",
                "block_index": 0,
                "method": "U0-aligned",
                "history_id": "07741c45",
            },
            quality_live={"quality_and_correctness": {"missing": True}},
            quality={"missing": True},
            derived={"status": "PASS"},
        )


def test_default_verify_block_rejects_block_output_seal_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    root = tmp_path / "block"
    root.mkdir()
    quality_body = {"quality_payload": "test"}
    quality = {
        **quality_body,
        "quality_and_correctness_sha256": _payload_sha256(quality_body),
    }
    (root / "quality_and_correctness.json").write_text(
        json.dumps(quality), encoding="utf-8"
    )
    derived = {"status": "PASS", "block_index": 0}
    monkeypatch.setattr(
        module,
        "derive_aligned_block_output",
        lambda *_args, **_kwargs: derived,
    )
    output_body = {
        "schema_version": "membind.paper-eval-v3.membind-v1-aligned-block-output.v1",
        "status": "PASS",
        "aligned_run_id": "aligned-main-test-001",
        "block_index": 0,
        "method": "U0-aligned",
        "history_id": "07741c45",
        "quality_and_correctness_sha256": quality["quality_and_correctness_sha256"],
        "derived": derived,
    }
    output = {**output_body, "block_output_sha256": _payload_sha256(output_body)}
    (root / "block_output.json").write_text(json.dumps(output), encoding="utf-8")
    assert module._default_verify_block(
        block_root=root,
        block={"block_index": 0},
        plan={},
    ) == derived

    output["block_output_sha256"] = "0" * 64
    (root / "block_output.json").write_text(json.dumps(output), encoding="utf-8")
    with pytest.raises(module.RunnerError, match="block output seal"):
        module._default_verify_block(
            block_root=root,
            block={"block_index": 0},
            plan={},
        )


def test_command_rejects_noncanonical_ids_before_creating_any_artifact_root(
    tmp_path: Path,
) -> None:
    module = _module()
    world = _World()
    with pytest.raises(module.RunnerError, match="aligned run id invalid"):
        module.run_aligned_main_table(
            aligned_run_id="aligned-main-test-001/../escape",
            main_table_run_id="main-table-main-test-001",
            membind_runs_root=tmp_path / "membind-runs",
            aligned_table_runs_root=tmp_path / "aligned-runs",
            hooks=world.hooks(module),
        )
    assert not (tmp_path / "membind-runs").exists()
    assert not (tmp_path / "aligned-runs").exists()

    with pytest.raises(module.RunnerError, match="main table run id invalid"):
        module.run_aligned_main_table(
            aligned_run_id="aligned-main-test-001",
            main_table_run_id="main-table-main-test-001/../escape",
            membind_runs_root=tmp_path / "membind-runs-2",
            aligned_table_runs_root=tmp_path / "aligned-runs-2",
            hooks=world.hooks(module),
        )
    assert not (tmp_path / "membind-runs-2").exists()
    assert not (tmp_path / "aligned-runs-2").exists()


def test_command_refuses_to_overwrite_a_drifted_existing_run_manifest(tmp_path: Path) -> None:
    module = _module()
    kwargs = {
        "aligned_run_id": "aligned-main-test-001",
        "main_table_run_id": "main-table-main-test-001",
        "membind_runs_root": tmp_path / "membind-runs",
        "aligned_table_runs_root": tmp_path / "aligned-runs",
    }
    module.run_aligned_main_table(**kwargs, hooks=_World().hooks(module))
    manifest_path = tmp_path / "membind-runs/aligned-main-test-001/RUN_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["execution_identity_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    second = _World()
    with pytest.raises(module.RunnerError, match="existing run manifest drift"):
        module.run_aligned_main_table(**kwargs, hooks=second.hooks(module))
    assert second.executed == []
    assert second.quality_runtime_closed == 0
