"""Stable, content-free causal identity for fine-grained memory operators."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


class OperatorIdentityError(ValueError):
    """An operator identity is incomplete or ambiguous."""


def _fail(code: str) -> OperatorIdentityError:
    return OperatorIdentityError(code)


def _identity(value: object, code: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise _fail(code)
    return value


def _ordinal(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


@dataclass(frozen=True, slots=True)
class OperatorIdentity:
    """Identity derived only from explicit workflow attribution metadata."""

    history_id: str
    source_id: int
    operator_role: str
    operator_ordinal: int
    parent_bind_id: str
    parent_operator_id: str | None
    operator_id: str

    @classmethod
    def create(
        cls,
        *,
        history_id: str,
        source_id: int,
        operator_role: str,
        operator_ordinal: int,
        parent_bind_id: str,
        parent_operator_id: str | None = None,
    ) -> "OperatorIdentity":
        history = _identity(history_id, "history_id_invalid")
        source = _ordinal(source_id, "source_id_invalid")
        role = _identity(operator_role, "operator_role_invalid")
        ordinal = _ordinal(operator_ordinal, "operator_ordinal_invalid")
        parent_bind = _identity(parent_bind_id, "parent_bind_id_invalid")
        if parent_operator_id is not None:
            parent_operator_id = _identity(
                parent_operator_id,
                "parent_operator_id_invalid",
            )
        identity_payload = {
            "history_id": history,
            "operator_ordinal": ordinal,
            "operator_role": role,
            "parent_bind_id": parent_bind,
            "parent_operator_id": parent_operator_id,
            "source_id": source,
        }
        serialized = json.dumps(
            identity_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        operator_id = "mseg-op-" + hashlib.sha256(serialized).hexdigest()[:32]
        return cls(
            history_id=history,
            source_id=source,
            operator_role=role,
            operator_ordinal=ordinal,
            parent_bind_id=parent_bind,
            parent_operator_id=parent_operator_id,
            operator_id=operator_id,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "history_id": self.history_id,
            "source_id": self.source_id,
            "operator_id": self.operator_id,
            "operator_role": self.operator_role,
            "operator_ordinal": self.operator_ordinal,
            "parent_bind_id": self.parent_bind_id,
            "parent_operator_id": self.parent_operator_id,
        }
