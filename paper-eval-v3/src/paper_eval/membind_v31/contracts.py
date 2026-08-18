"""Static operator contracts for the MemBind v3.1 state cut.

This module has no runtime or backend dependency.  It defines the two
dependency classes and four effect classes frozen by the methodology, then
fails closed on combinations that could incorrectly enter the Compile Region.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from paper_eval.artifacts import payload_sha256


class OperatorContractError(ValueError):
    """An operator declaration is malformed or violates the state cut."""


def _fail(code: str) -> OperatorContractError:
    return OperatorContractError(code)


class DependencyClass(str, Enum):
    """Whether an operator is bounded by arrived evidence or mutable state."""

    EVIDENCE_BOUND = "EVIDENCE_BOUND"
    STATE_BOUND = "STATE_BOUND"


class EffectClass(str, Enum):
    """The strongest persistent-state effect declared by an operator."""

    PURE = "PURE"
    STATE_READ = "STATE_READ"
    STATE_WRITE = "STATE_WRITE"
    PUBLISH = "PUBLISH"


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise _fail(code)
    return value


def _dependency(value: object) -> DependencyClass:
    if not isinstance(value, DependencyClass):
        raise _fail("dependency_class_invalid")
    return value


def _effect(value: object) -> EffectClass:
    if not isinstance(value, EffectClass):
        raise _fail("effect_class_invalid")
    return value


@dataclass(frozen=True, slots=True)
class OperatorContract:
    """A self-hashing static declaration for one construction operator."""

    operator_name: str
    dependency_class: DependencyClass
    effect_class: EffectClass
    contract_sha256: str

    @classmethod
    def create(
        cls,
        *,
        operator_name: str,
        dependency_class: DependencyClass,
        effect_class: EffectClass,
    ) -> "OperatorContract":
        name = _text(operator_name, "operator_name_invalid")
        dependency = _dependency(dependency_class)
        effect = _effect(effect_class)
        cls._validate_pair(dependency, effect)
        payload = cls._payload_from_parts(
            operator_name=name,
            dependency_class=dependency,
            effect_class=effect,
        )
        return cls(
            operator_name=name,
            dependency_class=dependency,
            effect_class=effect,
            contract_sha256=payload_sha256(payload),
        )

    @staticmethod
    def _validate_pair(
        dependency_class: DependencyClass,
        effect_class: EffectClass,
    ) -> None:
        if (
            dependency_class is DependencyClass.EVIDENCE_BOUND
            and effect_class is not EffectClass.PURE
        ):
            raise _fail("evidence_bound_must_be_pure")

    @staticmethod
    def _payload_from_parts(
        *,
        operator_name: str,
        dependency_class: DependencyClass,
        effect_class: EffectClass,
    ) -> dict[str, str]:
        return {
            "dependency_class": dependency_class.value,
            "effect_class": effect_class.value,
            "operator_name": operator_name,
        }

    @property
    def compile_eligible(self) -> bool:
        return (
            self.dependency_class is DependencyClass.EVIDENCE_BOUND
            and self.effect_class is EffectClass.PURE
        )

    def payload(self) -> dict[str, str]:
        return self._payload_from_parts(
            operator_name=self.operator_name,
            dependency_class=self.dependency_class,
            effect_class=self.effect_class,
        )

    def verify(self) -> "OperatorContract":
        name = _text(self.operator_name, "operator_name_invalid")
        dependency = _dependency(self.dependency_class)
        effect = _effect(self.effect_class)
        self._validate_pair(dependency, effect)
        if not isinstance(self.contract_sha256, str) or self.contract_sha256 != payload_sha256(
            self._payload_from_parts(
                operator_name=name,
                dependency_class=dependency,
                effect_class=effect,
            )
        ):
            raise _fail("contract_hash_mismatch")
        return self


__all__ = [
    "DependencyClass",
    "EffectClass",
    "OperatorContract",
    "OperatorContractError",
]
