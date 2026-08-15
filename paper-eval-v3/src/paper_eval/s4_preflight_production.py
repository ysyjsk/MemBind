"""Lazy, read-only production dependencies for the S4 service preflight."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

from .artifacts import payload_sha256
from .s4_preflight import (
    CONSTRUCTION_BASE_URL,
    CONSTRUCTION_SERVER_URL,
    EMBEDDING_BASE_URL,
    HISTORICAL_S1_NAMESPACE,
    collect_s4_preflight,
)


_ENV_FIELDS = (
    "NEO4J_URI",
    "NEO4J_USER",
    "NEO4J_PASSWORD",
    "CONSTRUCTION_LLM_API_KEY",
    "EMBEDDING_API_KEY",
)


def _validate_env(value: Mapping[str, str]) -> dict[str, str]:
    selected = {field: str(value.get(field, "")) for field in _ENV_FIELDS}
    if selected["NEO4J_URI"] != "bolt://localhost:7687":
        raise ValueError("S4 preflight Neo4j URI drift")
    if not selected["NEO4J_USER"] or not selected["NEO4J_PASSWORD"]:
        raise ValueError("S4 preflight Neo4j credentials are missing")
    return selected


def load_s4_preflight_env(path: Path) -> dict[str, str]:
    """Read only required fields from the ignored env file without mutation."""

    selected: dict[str, str] = {}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise ValueError(
            f"S4 preflight env is unreadable: {type(error).__name__}"
        ) from None
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, raw_value = line.partition("=")
        key = key.strip()
        if not separator or key not in _ENV_FIELDS:
            continue
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        selected[key] = value
    return _validate_env(selected)


def load_expected_s1_state(path: Path) -> dict[str, Any]:
    """Load the hash-valid completed S1 checkpoint used as read-only anchor."""

    try:
        checkpoint = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(
            f"S1 checkpoint is unreadable: {type(error).__name__}"
        ) from None
    if not isinstance(checkpoint, dict):
        raise ValueError("S1 checkpoint is not a mapping")
    body = dict(checkpoint)
    stored_hash = body.pop("payload_sha256", None)
    if not isinstance(stored_hash, str) or stored_hash != payload_sha256(body):
        raise ValueError("S1 checkpoint payload hash mismatch")
    if (
        checkpoint.get("schema_version")
        != "membind.paper-eval-v3.s1-checkpoint.v1"
        or checkpoint.get("run_id") != "s1-20260814-001"
        or checkpoint.get("history_id") != "07741c45"
        or checkpoint.get("namespace") != HISTORICAL_S1_NAMESPACE
        or checkpoint.get("status") != "completed"
        or checkpoint.get("completed_source_sequences") != list(range(49))
        or not isinstance(checkpoint.get("namespace_state"), dict)
    ):
        raise ValueError("S1 checkpoint identity or completion drift")
    return dict(checkpoint["namespace_state"])


class _Neo4jReadOnlyProbe:
    def __init__(self, driver: Any) -> None:
        self.driver = driver

    async def connectivity(self) -> bool:
        await self.driver.verify_connectivity()
        return True

    async def namespace_state(self, namespace: str) -> dict[str, Any]:
        result = await self.driver.execute_query(
            """
            CALL {
              MATCH (n)
              WHERE n.group_id = $group_id
              RETURN collect(n) AS nodes
            }
            CALL {
              MATCH ()-[r]->()
              WHERE r.group_id = $group_id
              RETURN count(r) AS relationship_count
            }
            RETURN size(nodes) AS node_count, relationship_count,
                   [n IN nodes WHERE n:Episodic | n.name] AS episode_names
            """,
            parameters_={"group_id": namespace},
            database_="neo4j",
        )
        records = getattr(result, "records", None)
        if records is None and isinstance(result, tuple) and result:
            records = result[0]
        if not isinstance(records, list) or len(records) != 1:
            raise RuntimeError("S4 preflight namespace query row-count drift")
        record = records[0]
        row = record if isinstance(record, dict) else dict(record)
        return {
            "node_count": int(row.get("node_count") or 0),
            "relationship_count": int(row.get("relationship_count") or 0),
            "episode_names": sorted(
                str(name) for name in row.get("episode_names") or []
            ),
        }

    async def close(self) -> None:
        await self.driver.close()


def _production_dependencies(
    env: Mapping[str, str],
) -> tuple[Callable[[str, str], Awaitable[Mapping[str, Any]]], _Neo4jReadOnlyProbe]:
    import httpx
    from neo4j import AsyncGraphDatabase

    selected = _validate_env(env)

    async def get_json(base_url: str, path: str) -> Mapping[str, Any]:
        if base_url not in {
            CONSTRUCTION_BASE_URL,
            CONSTRUCTION_SERVER_URL,
            EMBEDDING_BASE_URL,
        }:
            raise ValueError("S4 preflight HTTP base URL drift")
        if path not in {"/models", "/version"}:
            raise ValueError("S4 preflight HTTP path is not read-only")
        api_key = (
            selected["CONSTRUCTION_LLM_API_KEY"]
            if base_url in {CONSTRUCTION_BASE_URL, CONSTRUCTION_SERVER_URL}
            else selected["EMBEDDING_API_KEY"]
        )
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        async with httpx.AsyncClient(timeout=20.0, trust_env=False) as client:
            response = await client.get(
                base_url.rstrip("/") + path,
                headers=headers,
            )
            response.raise_for_status()
            value = response.json()
        if not isinstance(value, Mapping):
            raise ValueError("S4 preflight HTTP response is not a mapping")
        return dict(value)

    driver = AsyncGraphDatabase.driver(
        selected["NEO4J_URI"],
        auth=(selected["NEO4J_USER"], selected["NEO4J_PASSWORD"]),
    )
    return get_json, _Neo4jReadOnlyProbe(driver)


async def execute_production_preflight(
    *,
    env: Mapping[str, str],
    s1_checkpoint_path: Path,
    get_json: Callable[[str, str], Awaitable[Mapping[str, Any]] | Mapping[str, Any]] | None = None,
    neo4j_probe: Any | None = None,
    capture_namespace: str | None = None,
    replay_namespace: str | None = None,
) -> dict[str, Any]:
    """Execute the bounded reads and always close the Neo4j driver."""

    selected_env = _validate_env(env)
    expected_s1 = load_expected_s1_state(Path(s1_checkpoint_path))
    selected_get_json = get_json
    selected_probe = neo4j_probe
    if selected_get_json is None or selected_probe is None:
        if selected_get_json is not None or selected_probe is not None:
            raise ValueError("partial S4 preflight dependency injection")
        selected_get_json, selected_probe = _production_dependencies(selected_env)
    try:
        kwargs: dict[str, Any] = {}
        if capture_namespace is not None:
            kwargs["capture_namespace"] = capture_namespace
        if replay_namespace is not None:
            kwargs["replay_namespace"] = replay_namespace
        return await collect_s4_preflight(
            get_json=selected_get_json,
            neo4j_connectivity=selected_probe.connectivity,
            namespace_state=selected_probe.namespace_state,
            expected_historical_s1_state=expected_s1,
            **kwargs,
        )
    finally:
        await selected_probe.close()
