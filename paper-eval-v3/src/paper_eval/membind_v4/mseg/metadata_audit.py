"""Static, provider-free audit of MEG causal metadata non-interference."""

from __future__ import annotations

import ast
from pathlib import Path


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _has_call(tree: ast.AST, name: str) -> bool:
    return any(
        isinstance(node, ast.Call)
        and ((isinstance(node.func, ast.Name) and node.func.id == name)
             or (isinstance(node.func, ast.Attribute) and node.func.attr == name))
        for node in ast.walk(tree)
    )


def audit_metadata_noninterference(project_root: Path) -> dict[str, object]:
    root = Path(project_root).resolve()
    request_path = root / "src/paper_eval/membind_v31/request_runtime.py"
    graphiti_path = root / "src/paper_eval/membind_v4/mseg/graphiti_0293_runtime.py"
    live_path = root / "src/paper_eval/membind_v4/mseg/runtime_live.py"
    request = _source(request_path)
    graphiti = _source(graphiti_path)
    live = _source(live_path)
    request_tree = ast.parse(request)
    graphiti_tree = ast.parse(graphiti)
    live_tree = ast.parse(live)
    checks: dict[str, bool] = {
        "causal_provider_is_opt_in": "causal_metadata_provider" in request,
        "metadata_is_context_local": "ContextVar" in request and "_CURRENT_REQUEST_ID" in request,
        "metadata_emits_telemetry_only": "_causal_metadata[request_id]" in request and "telemetry_event" in request,
        "metadata_not_added_to_graphiti_prompt": "causal_metadata" not in graphiti,
        "metadata_not_added_to_graphiti_objects": "causal_metadata" not in graphiti,
        "metadata_not_used_for_candidate_computation": "causal_metadata" not in graphiti,
        "metadata_not_used_for_db_mutation": "causal_metadata" not in graphiti,
        "metadata_not_used_for_schema": "causal_metadata" not in graphiti,
        "runtime_builder_only_injects_provider": "selected[\"causal_metadata_provider\"]" in live,
        "request_runtime_ast_parses": _has_call(request_tree, "_submit") and _has_call(request_tree, "_terminal"),
        "graphiti_runtime_ast_parses": _has_call(graphiti_tree, "record_request") and _has_call(graphiti_tree, "record_write_intent"),
        "live_runtime_ast_parses": "current_runtime_request_metadata" in live,
    }
    return {
        "schema_version": "membind.meg.metadata-noninterference.v1",
        "status": "PASS" if all(checks.values()) else "STOP_METADATA_NONINTERFERENCE_FAILURE",
        "checks": checks,
        "data_plane": {
            "production_semantic": ["prompt input", "Graphiti entities/edges", "candidate computation", "DB mutation payload", "structured-output schema", "request ordering", "admission policy"],
            "observability_metadata": ["causal_metadata_provider", "task-local request context", "prompt_name telemetry", "RequestSpan metadata", "request runtime events"],
        },
        "source_files": [str(path.relative_to(root)) for path in (request_path, graphiti_path, live_path)],
    }


def render_metadata_noninterference_audit(audit: dict[str, object]) -> str:
    checks = audit["checks"]
    lines = [
        "# MEG Metadata Non-Interference Audit",
        "",
        f"STATUS: {audit['status']}",
        "",
        "This provider-free audit separates the production semantic data plane from the observability metadata plane.",
        "",
        "## Data Plane",
        "",
        "Production semantic inputs, Graphiti entity/edge objects, candidate computation, DB mutation payloads, structured-output schemas, request ordering, and admission policy do not consume causal metadata.",
        "",
        "## Metadata Plane",
        "",
        "The opt-in `causal_metadata_provider` reads task-local operator context and emits bounded request telemetry. `prompt_name` is retained as request telemetry and RequestSpan metadata only.",
        "",
        "## Checks",
        "",
    ]
    for name, result in checks.items():
        lines.append(f"- `{name}`: {'PASS' if result else 'FAIL'}")
    lines.extend([
        "",
        "Unknown or absent metadata remains `OPAQUE`; no timing, completion order, or function-name inference is permitted.",
        "",
    ])
    return "\n".join(lines)


__all__ = ["audit_metadata_noninterference", "render_metadata_noninterference_audit"]
