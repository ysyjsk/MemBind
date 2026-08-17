"""Public code/configuration identities for MemBind-v1 prepared artifacts.

Prepared node artifacts can survive a process interruption.  Their identity
therefore needs to bind the narrow node-only semantic boundary to the public
runtime envelope and implementation fingerprints, without ever retaining a
credential, request body, or prompt content.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy

from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v1.graphiti_adapter import NodeArtifactIdentity


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IMPLEMENTATION_KEYS = {
    "aligned_live",
    "graphiti_adapter",
    "graphiti_factories",
    "semantic_trace_binding",
}
_PRIVATE_KEYS = {
    "api_key",
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
}


class MemBindV1ExecutionIdentityError(ValueError):
    """A public execution envelope cannot safely bind a prepared artifact."""


def _fail(code: str) -> MemBindV1ExecutionIdentityError:
    return MemBindV1ExecutionIdentityError(code)


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def _private_free(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or key.casefold() in _PRIVATE_KEYS:
                raise _fail("private execution identity field")
            _private_free(child)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _private_free(child)
        return
    if value is None or isinstance(value, (str, int, bool)):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    raise _fail("execution identity invalid")


def _public_runtime(value: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise _fail("runtime identity invalid")
    runtime = deepcopy(dict(value))
    _private_free(runtime)
    expected = {
        "schema_version",
        "construction",
        "embedding",
        "neo4j",
        "graphiti_max_coroutines",
        "global_llm_admission_k",
    }
    if set(runtime) != expected or runtime.get("schema_version") != (
        "membind.paper-eval-v3.membind-v1-live-runtime.v1"
    ):
        raise _fail("runtime identity invalid")
    if runtime.get("global_llm_admission_k") != 2:
        raise _fail("global LLM admission invalid")
    if runtime.get("graphiti_max_coroutines") != 8:
        raise _fail("runtime identity invalid")
    construction = runtime.get("construction")
    embedding = runtime.get("embedding")
    neo4j = runtime.get("neo4j")
    if not isinstance(construction, Mapping) or not isinstance(embedding, Mapping) or not isinstance(neo4j, Mapping):
        raise _fail("runtime identity invalid")
    if set(construction) != {
        "base_url",
        "served_model_id",
        "requested_max_tokens",
        "structured_output_mode",
    } or set(embedding) != {"base_url", "served_model_id", "dimension"} or set(neo4j) != {"uri"}:
        raise _fail("runtime identity invalid")
    for mapping in (construction, embedding, neo4j):
        for child in mapping.values():
            if not isinstance(child, (str, int)) or isinstance(child, bool):
                raise _fail("runtime identity invalid")
    if construction.get("requested_max_tokens") != 16_384 or construction.get("structured_output_mode") != "json_schema":
        raise _fail("runtime identity invalid")
    if embedding.get("dimension") != 1024:
        raise _fail("runtime identity invalid")
    return runtime


def _implementation(value: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != _IMPLEMENTATION_KEYS:
        raise _fail("implementation identity invalid")
    return {key: _sha(item, "implementation identity invalid") for key, item in value.items()}


def build_node_artifact_identity(
    *,
    runtime_identity: Mapping[str, object],
    implementation_hashes: Mapping[str, str],
) -> NodeArtifactIdentity:
    """Build the five immutable fields expected by ``PreparedNodeArtifact``.

    The individual fields intentionally correspond to independent reviewer
    questions: what was moved, which model envelope was used, which prompt
    implementation was selected, which durable output schema applied, and
    which execution controls were active.
    """

    runtime = _public_runtime(runtime_identity)
    implementation = _implementation(implementation_hashes)
    construction = dict(runtime["construction"])
    return NodeArtifactIdentity(
        operation_identity_sha256=payload_sha256(
            {
                "candidate": "MemBind-v1 node-only",
                "prepare_operation": "Graphiti.extract_nodes",
                "mutable_graph_access": "forbidden",
                "evidence_fence": "last_n=10 chronological fail-closed",
                "graphiti_adapter_sha256": implementation["graphiti_adapter"],
                "factory_sha256": implementation["graphiti_factories"],
            }
        ),
        model_identity_sha256=payload_sha256(construction),
        prompt_identity_sha256=payload_sha256(
            {
                "prepare_operation": "Graphiti.extract_nodes",
                "graphiti_adapter_sha256": implementation["graphiti_adapter"],
                "semantic_trace_binding_sha256": implementation[
                    "semantic_trace_binding"
                ],
            }
        ),
        schema_identity_sha256=payload_sha256(
            {
                "prepared_artifact_schema": "membind-v1-prepared-node-artifact.v1",
                "canonical_projection": "canonical-json",
                "factory_sha256": implementation["graphiti_factories"],
            }
        ),
        config_identity_sha256=payload_sha256(
            {
                "runtime_identity": runtime,
                "compile_concurrency": 1,
                "prepared_lookahead": 1,
                "global_llm_admission_k": 2,
                "bind_workers": 1,
                "frontier_policy": "source-ordered-frontier-first",
                "aligned_live_sha256": implementation["aligned_live"],
            }
        ),
    )


__all__ = [
    "MemBindV1ExecutionIdentityError",
    "build_node_artifact_identity",
]
