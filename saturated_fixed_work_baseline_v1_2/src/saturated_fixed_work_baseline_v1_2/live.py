"""Frozen live block identities independent of Graphiti scheduling internals."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .dataset import EXPECTED_EPISODE_COUNTS
from .schedules import Method


_RUN_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,79}$")


FORMAL_ORDER = (
    ("07741c45", Method.B0_NATIVE_SERIAL),
    ("07741c45", Method.B1_NAIVE_WHOLE_UPDATE_ASYNC),
    ("b6019101", Method.B1_NAIVE_WHOLE_UPDATE_ASYNC),
    ("b6019101", Method.B0_NATIVE_SERIAL),
    ("6071bd76", Method.B0_NATIVE_SERIAL),
    ("6071bd76", Method.B1_NAIVE_WHOLE_UPDATE_ASYNC),
    ("a2f3aa27", Method.B1_NAIVE_WHOLE_UPDATE_ASYNC),
    ("a2f3aa27", Method.B0_NATIVE_SERIAL),
)


@dataclass(frozen=True, slots=True)
class FormalBlock:
    ordinal: int
    block_id: str
    run_id: str
    history_id: str
    method: Method
    attempt_ordinal: int
    namespace: str
    cache_salt: str


def _validate_identity(
    run_id: str, method: Method, history_id: str, attempt_ordinal: int
) -> None:
    if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
        raise ValueError("RUN_ID_INVALID")
    if not isinstance(method, Method):
        raise ValueError("METHOD_INVALID")
    if history_id not in EXPECTED_EPISODE_COUNTS:
        raise ValueError("HISTORY_NOT_FROZEN")
    if isinstance(attempt_ordinal, bool) or not isinstance(attempt_ordinal, int) or attempt_ordinal < 1:
        raise ValueError("ATTEMPT_ORDINAL_INVALID")


def derive_namespace(
    run_id: str,
    method: Method,
    history_id: str,
    *,
    attempt_ordinal: int,
) -> str:
    _validate_identity(run_id, method, history_id, attempt_ordinal)
    return (
        f"sfwb-v1-2/{method.value}/{history_id}/{run_id}/"
        f"attempt-{attempt_ordinal:03d}"
    )


def derive_cache_salt(
    run_id: str, block_id: str, *, attempt_ordinal: int
) -> str:
    if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
        raise ValueError("RUN_ID_INVALID")
    if not isinstance(block_id, str) or not block_id:
        raise ValueError("BLOCK_ID_INVALID")
    if isinstance(attempt_ordinal, bool) or not isinstance(attempt_ordinal, int) or attempt_ordinal < 1:
        raise ValueError("ATTEMPT_ORDINAL_INVALID")
    digest = hashlib.sha256(
        f"SATURATED_FIXED_WORK_CONSTRUCTION_PROTOCOL_V1_2\0{run_id}\0"
        f"{block_id}\0{attempt_ordinal}".encode("ascii")
    ).hexdigest()
    return f"sfwb12-{digest[:56]}"


def build_formal_plan(
    run_id: str, *, attempt_ordinal: int = 1
) -> tuple[FormalBlock, ...]:
    rows: list[FormalBlock] = []
    for ordinal, (history_id, method) in enumerate(FORMAL_ORDER, start=1):
        block_id = f"formal-{ordinal:03d}-{history_id}-{method.value}"
        rows.append(
            FormalBlock(
                ordinal=ordinal,
                block_id=block_id,
                run_id=run_id,
                history_id=history_id,
                method=method,
                attempt_ordinal=attempt_ordinal,
                namespace=derive_namespace(
                    run_id,
                    method,
                    history_id,
                    attempt_ordinal=attempt_ordinal,
                ),
                cache_salt=derive_cache_salt(
                    run_id, block_id, attempt_ordinal=attempt_ordinal
                ),
            )
        )
    return tuple(rows)


__all__ = [
    "FORMAL_ORDER",
    "FormalBlock",
    "build_formal_plan",
    "derive_cache_salt",
    "derive_namespace",
]
