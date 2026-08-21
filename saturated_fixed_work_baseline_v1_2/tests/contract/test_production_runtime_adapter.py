from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from saturated_fixed_work_baseline_v1_2.production_runtime import (
    ProductionRuntimeError,
    build_protocol_runtime,
)


def _authority(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "protocol_version": "SATURATED_FIXED_WORK_CONSTRUCTION_PROTOCOL_V1_2",
                "run_id": "sfwb-v1-2-runtime-001",
                "block_id": "formal-001",
                "namespace": "namespace",
            }
        ),
        encoding="utf-8",
    )


def test_runtime_adapter_authorizes_native_builder_and_installs_only_salt(
    tmp_path: Path,
) -> None:
    authority = tmp_path / "authority.json"
    _authority(authority)
    calls: list[str] = []
    runtime = SimpleNamespace(
        graphiti=SimpleNamespace(),
        llm_client=SimpleNamespace(client=SimpleNamespace()),
        embedder=SimpleNamespace(client=SimpleNamespace()),
        reranker=SimpleNamespace(client=SimpleNamespace()),
    )

    def builder(**kwargs: object) -> object:
        assert authority.is_file()
        calls.append("builder")
        authorization_checker = kwargs["authorization_checker"]
        authorization_checker(kwargs["live_action"])
        return runtime

    def salt_installer(value: object, cache_salt: str) -> object:
        assert value is runtime
        assert cache_salt == "formal-salt"
        calls.append("salt")
        return value

    result = build_protocol_runtime(
        repository_root=tmp_path,
        cache_salt="formal-salt",
        authority_path=authority,
        builder=builder,
        env_loader=lambda: None,
        live_action="NATIVE_CHARACTERIZATION_C0",
        salt_installer=salt_installer,
    )
    assert result is runtime
    assert calls == ["builder", "salt"]
    assert not hasattr(result, "admission")


def test_runtime_adapter_refuses_missing_or_wrong_authority(tmp_path: Path) -> None:
    with pytest.raises(ProductionRuntimeError, match="LIVE_AUTHORITY_UNREADABLE"):
        build_protocol_runtime(
            repository_root=tmp_path,
            cache_salt="salt",
            authority_path=tmp_path / "missing.json",
            builder=lambda **kwargs: object(),
            env_loader=lambda: None,
            live_action="NATIVE_CHARACTERIZATION_C0",
        )
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"protocol_version": "old"}), encoding="utf-8")
    with pytest.raises(ProductionRuntimeError, match="LIVE_AUTHORITY_PROTOCOL_MISMATCH"):
        build_protocol_runtime(
            repository_root=tmp_path,
            cache_salt="salt",
            authority_path=wrong,
            builder=lambda **kwargs: object(),
            env_loader=lambda: None,
            live_action="NATIVE_CHARACTERIZATION_C0",
        )


def test_production_runtime_has_no_forbidden_scheduler_or_admission_imports(
    repository_root: Path,
) -> None:
    source = (
        repository_root
        / "saturated_fixed_work_baseline_v1_2/src/saturated_fixed_work_baseline_v1_2/production_runtime.py"
    )
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
    assert not any("admission" in name for name in imports)
    assert not any("membind_v5_oracle" in name for name in imports)
    text = source.read_text(encoding="utf-8")
    assert "RequestAdmission" not in text
    assert "execute_method_schedule" not in text
