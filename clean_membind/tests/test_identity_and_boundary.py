import ast
from pathlib import Path

from membind.backends import BackendConfig
from membind.governance.identity import implementation_identity


def test_backend_identity_is_deterministic_and_explicit():
    config = BackendConfig()
    assert config.model == "qwen2.5:14b"
    assert config.graphiti_version == "0.29.3"
    assert config.structured_output_mode == "json_schema"
    assert len(config.identity_sha256) == 64


def test_clean_core_does_not_import_legacy_runtime():
    core = Path(__file__).parents[1] / "src" / "membind" / "core"
    imports = []
    for path in core.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports.extend(
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        )
        imports.extend(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
    source_imports = " ".join(imports).casefold()
    for forbidden in ("qwen", "vllm", "ollama", "graphiti", "memoryagentbench"):
        assert forbidden not in source_imports


def test_identity_has_hash():
    identity = implementation_identity(Path(__file__).parents[1])
    assert len(identity["identity_sha256"]) == 64
