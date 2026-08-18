#!/usr/bin/env python3
"""Exactly clean one failed MemBind v3.1 block namespace and seal evidence."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Awaitable, Callable
from typing import Any, Mapping


PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
SOURCE = PROJECT / "src"
LEGACY = ROOT / "membind-validation"
for path in (SOURCE, LEGACY / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from paper_eval.artifacts import atomic_write_json, payload_sha256, sha256_file  # noqa: E402
from paper_eval.membind_v31.method_plan import verify_membind_v31_method_plan  # noqa: E402
from paper_eval.s1_live import S1LiveAdapter  # noqa: E402
from paper_eval.s4_preflight_production import load_s4_preflight_env  # noqa: E402


SCHEMA = "membind.paper-eval-v3.membind-v31-exact-namespace-cleanup.v1"
ATTEMPT_SCHEMA = "membind.paper-eval-v3.membind-v31-single-history-feasibility.v1"
CHECKPOINT_SCHEMA = "membind.paper-eval-v3.membind-v31-single-history-checkpoint.v1"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")


class CleanupError(ValueError):
    """A cleanup input or postcondition is unsafe."""


def _fail(code: str) -> CleanupError:
    return CleanupError(code)


def _sealed(value: Mapping[str, Any], *, code: str) -> dict[str, Any]:
    selected = dict(value)
    stored = selected.pop("payload_sha256", None)
    if not isinstance(stored, str) or stored != payload_sha256(selected):
        raise _fail(code)
    selected["payload_sha256"] = stored
    return selected


def _read_json(path: Path, *, code: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None
    if not isinstance(value, dict):
        raise _fail(code)
    return value


def validate_failed_attempt(
    manifest: Mapping[str, Any],
    *,
    expected_attempt_id: str,
    expected_namespace: str,
    checkpoint: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind cleanup authority to one sealed, non-reusable failed attempt."""

    selected = _sealed(manifest, code="manifest_hash_invalid")
    if (
        selected.get("schema_version") != ATTEMPT_SCHEMA
        or selected.get("attempt_id") != expected_attempt_id
        or selected.get("namespace") != expected_namespace
        or _ID.fullmatch(expected_attempt_id) is None
        or _ID.fullmatch(expected_namespace) is None
    ):
        raise _fail("failed_attempt_identity_invalid")
    if checkpoint is None:
        if selected.get("status") != "FAILED_NON_REUSABLE":
            raise _fail("failed_attempt_status_invalid")
        return selected

    sealed_checkpoint = _sealed(checkpoint, code="checkpoint_hash_invalid")
    block = sealed_checkpoint.get("block_checkpoint")
    if (
        sealed_checkpoint.get("schema_version") != CHECKPOINT_SCHEMA
        or sealed_checkpoint.get("attempt_id") != expected_attempt_id
        or sealed_checkpoint.get("status") != "FAILED_NON_REUSABLE"
        or not isinstance(block, Mapping)
        or block.get("terminal_status") != "INCOMPLETE_NON_MERGEABLE"
        or block.get("complete_coverage") is not False
    ):
        raise _fail("failed_attempt_status_invalid")
    return selected


def _count(state: Mapping[str, Any], field: str) -> int:
    value = state.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise _fail("namespace_state_invalid")
    return value


def build_cleanup_evidence(
    *,
    attempt_id: str,
    namespace: str,
    pre_state: Mapping[str, Any],
    post_state: Mapping[str, Any],
    observed_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build the only evidence shape accepted by the feasibility launcher."""

    if _ID.fullmatch(attempt_id) is None or _ID.fullmatch(namespace) is None:
        raise _fail("cleanup_identity_invalid")
    pre_nodes = _count(pre_state, "node_count")
    pre_relationships = _count(pre_state, "relationship_count")
    post_nodes = _count(post_state, "node_count")
    post_relationships = _count(post_state, "relationship_count")
    if post_nodes != 0 or post_relationships != 0:
        raise _fail("post_cleanup_nonzero")
    timestamp = observed_at_utc or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    body: dict[str, Any] = {
        "schema_version": SCHEMA,
        "status": "PASS",
        "source_failed_attempt_id": attempt_id,
        "namespace": namespace,
        "scope": "EXACT_GROUP_ID_ONLY",
        "group_ids": [namespace],
        "global_cleanup_used": False,
        "pre_cleanup_node_count": pre_nodes,
        "pre_cleanup_relationship_count": pre_relationships,
        "post_cleanup_node_count": post_nodes,
        "post_cleanup_relationship_count": post_relationships,
        "observed_at_utc": timestamp,
    }
    return {**body, "payload_sha256": payload_sha256(body)}


def _block_zero_namespace(plan_path: Path) -> str:
    plan = verify_membind_v31_method_plan(
        _read_json(plan_path, code="method_plan_unreadable")
    )
    block = plan.get("blocks", [None])[0]
    if not isinstance(block, Mapping) or block.get("history_id") != "07741c45":
        raise _fail("method_plan_block_zero_invalid")
    namespace = block.get("namespace")
    if not isinstance(namespace, str) or _ID.fullmatch(namespace) is None:
        raise _fail("method_plan_block_zero_invalid")
    return namespace


async def cleanup_exact_namespace(
    *,
    env_path: Path,
    namespace: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Delete only nodes owned by ``namespace`` and verify a zero post-state."""

    from graphiti_core.driver.neo4j_driver import Neo4jDriver
    from graphiti_core.utils.maintenance.graph_data_operations import clear_data

    env = load_s4_preflight_env(env_path)
    driver = Neo4jDriver(
        env["NEO4J_URI"], env["NEO4J_USER"], env["NEO4J_PASSWORD"]
    )
    try:
        before, after = await cleanup_exact_namespace_with_driver(
            driver=driver,
            namespace=namespace,
            clear_data_fn=clear_data,
        )
    finally:
        await driver.close()
    return before, after


async def cleanup_exact_namespace_with_driver(
    *,
    driver: Any,
    namespace: str,
    clear_data_fn: Callable[..., Awaitable[Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Testable exact-delete boundary used by the production cleanup path."""

    if _ID.fullmatch(namespace) is None or not callable(clear_data_fn):
        raise _fail("cleanup_identity_invalid")
    adapter = S1LiveAdapter(namespace)
    before = await adapter.namespace_state(driver)
    await clear_data_fn(driver, group_ids=[namespace])
    after = await adapter.namespace_state(driver)
    if int(after["node_count"]) != 0 or int(after["relationship_count"]) != 0:
        raise _fail("post_cleanup_nonzero")
    return before, after


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--failed-attempt-root", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--env", type=Path, default=LEGACY / ".env")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    namespace = _block_zero_namespace(args.plan)
    attempt_root = args.failed_attempt_root.resolve()
    if attempt_root.name != args.attempt_id:
        raise _fail("failed_attempt_root_invalid")
    manifest_path = attempt_root / "MANIFEST.json"
    checkpoint_path = attempt_root / "CHECKPOINT.json"
    validate_failed_attempt(
        _read_json(manifest_path, code="manifest_unreadable"),
        expected_attempt_id=args.attempt_id,
        expected_namespace=namespace,
        checkpoint=_read_json(checkpoint_path, code="checkpoint_unreadable"),
    )
    before, after = asyncio.run(
        cleanup_exact_namespace(env_path=args.env, namespace=namespace)
    )
    evidence = build_cleanup_evidence(
        attempt_id=args.attempt_id,
        namespace=namespace,
        pre_state=before,
        post_state=after,
    )
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output, evidence)
    print(
        json.dumps(
            {
                "status": evidence["status"],
                "source_failed_attempt_id": args.attempt_id,
                "namespace": namespace,
                "pre_cleanup_node_count": evidence["pre_cleanup_node_count"],
                "pre_cleanup_relationship_count": evidence[
                    "pre_cleanup_relationship_count"
                ],
                "post_cleanup_node_count": 0,
                "post_cleanup_relationship_count": 0,
                "artifact": str(output),
                "artifact_sha256": sha256_file(output),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
