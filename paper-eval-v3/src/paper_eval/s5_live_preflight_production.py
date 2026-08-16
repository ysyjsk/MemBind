"""Lazy read-only production collection for the S5 live preflight.

Imports that can create network clients occur only when the caller omits both
injected dependencies.  The bounded Neo4j query reads one exact group_id and
never creates, updates, deletes, or cleans a namespace.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from .s5_live_preflight import (
    CONSTRUCTION_BASE_URL,
    CONSTRUCTION_SERVER_URL,
    EMBEDDING_BASE_URL,
    collect_s5_live_preflight,
)


_ENV_FIELDS = (
    "NEO4J_URI",
    "NEO4J_USER",
    "NEO4J_PASSWORD",
    "CONSTRUCTION_LLM_API_KEY",
    "EMBEDDING_API_KEY",
)


class S5LivePreflightProductionError(ValueError):
    """Production preflight dependencies or their read-only scope are invalid."""


def _fail(code: str) -> S5LivePreflightProductionError:
    return S5LivePreflightProductionError(code)


def _validate_env(value: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise _fail("env_invalid")
    selected = {field: str(value.get(field, "")) for field in _ENV_FIELDS}
    if selected["NEO4J_URI"] != "bolt://localhost:7687":
        raise _fail("neo4j_uri_invalid")
    if not selected["NEO4J_USER"] or not selected["NEO4J_PASSWORD"]:
        raise _fail("neo4j_credentials_missing")
    return selected


class _Neo4jReadOnlyProbe:
    def __init__(self, driver: Any) -> None:
        self.driver = driver

    async def connectivity(self) -> bool:
        await self.driver.verify_connectivity()
        return True

    async def namespace_state(self, namespace: str) -> dict[str, int]:
        result = await self.driver.execute_query(
            """
            CALL {
              MATCH (n)
              WHERE n.group_id = $group_id
              RETURN count(n) AS node_count
            }
            CALL {
              MATCH ()-[r]->()
              WHERE r.group_id = $group_id
              RETURN count(r) AS relationship_count
            }
            RETURN node_count, relationship_count
            """,
            parameters_={"group_id": namespace},
            database_="neo4j",
        )
        records = getattr(result, "records", None)
        if records is None and isinstance(result, tuple) and result:
            records = result[0]
        if not isinstance(records, list) or len(records) != 1:
            raise _fail("namespace_query_result_invalid")
        record = records[0]
        row = record if isinstance(record, Mapping) else dict(record)
        return {
            "node_count": int(row.get("node_count") or 0),
            "relationship_count": int(row.get("relationship_count") or 0),
        }

    async def close(self) -> None:
        await self.driver.close()


def _production_dependencies(
    env: Mapping[str, str],
) -> tuple[
    Callable[[str, str], Awaitable[Mapping[str, Any]]],
    _Neo4jReadOnlyProbe,
]:
    import httpx
    from neo4j import AsyncGraphDatabase

    selected = _validate_env(env)

    async def get_json(base_url: str, path: str) -> Mapping[str, Any]:
        allowed = {
            (CONSTRUCTION_BASE_URL, "/models"),
            (CONSTRUCTION_SERVER_URL, "/version"),
            (EMBEDDING_BASE_URL, "/models"),
        }
        if (base_url, path) not in allowed:
            raise _fail("http_read_scope_invalid")
        api_key = (
            selected["CONSTRUCTION_LLM_API_KEY"]
            if base_url != EMBEDDING_BASE_URL
            else selected["EMBEDDING_API_KEY"]
        )
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        async with httpx.AsyncClient(timeout=20.0, trust_env=False) as client:
            response = await client.get(
                base_url.rstrip("/") + path,
                headers=headers,
            )
            response.raise_for_status()
            body = response.json()
        if not isinstance(body, Mapping):
            raise _fail("http_response_invalid")
        return dict(body)

    driver = AsyncGraphDatabase.driver(
        selected["NEO4J_URI"],
        auth=(selected["NEO4J_USER"], selected["NEO4J_PASSWORD"]),
    )
    return get_json, _Neo4jReadOnlyProbe(driver)


async def execute_s5_live_preflight_production(
    *,
    method: str,
    run_id: str,
    namespace: str,
    episode_source_sha256s: Sequence[str],
    production_identity_qualification: Mapping[str, object],
    production_identity_qualification_file_sha256: str,
    current_stage_pointer: Mapping[str, object],
    current_stage_pointer_file_sha256: str,
    env: Mapping[str, str],
    predecessor: Mapping[str, object] | None = None,
    fx0_qualification: Mapping[str, object] | None = None,
    get_json: Callable[
        [str, str], Awaitable[Mapping[str, Any]] | Mapping[str, Any]
    ]
    | None = None,
    neo4j_probe: Any | None = None,
) -> dict[str, Any]:
    """Execute bounded reads, evaluate them, and always close the probe."""

    selected_env = _validate_env(env)
    selected_get_json = get_json
    selected_probe = neo4j_probe
    if (selected_get_json is None) != (selected_probe is None):
        raise _fail("partial_dependency_injection")
    if selected_get_json is None:
        selected_get_json, selected_probe = _production_dependencies(selected_env)
    try:
        return await collect_s5_live_preflight(
            method=method,
            run_id=run_id,
            namespace=namespace,
            episode_source_sha256s=episode_source_sha256s,
            production_identity_qualification=production_identity_qualification,
            production_identity_qualification_file_sha256=(
                production_identity_qualification_file_sha256
            ),
            current_stage_pointer=current_stage_pointer,
            current_stage_pointer_file_sha256=current_stage_pointer_file_sha256,
            predecessor=predecessor,
            fx0_qualification=fx0_qualification,
            get_json=selected_get_json,
            neo4j_connectivity=selected_probe.connectivity,
            namespace_state=selected_probe.namespace_state,
        )
    finally:
        await selected_probe.close()


__all__ = [
    "S5LivePreflightProductionError",
    "execute_s5_live_preflight_production",
]
