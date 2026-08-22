"""Provider capability traps used by P1 preparation qualification."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class CapabilityViolation(RuntimeError):
    pass


class LLMOnlyFacade:
    """Expose only an injected LLM delegate; forbidden effects fail closed."""

    _forbidden = frozenset({"driver", "embedder", "persistent_store", "network", "neo4j"})

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate

    def __getattr__(self, name: str) -> Any:
        if name in self._forbidden:
            raise CapabilityViolation(f"forbidden preparation capability: {name}")
        return getattr(self._delegate, name)


@dataclass(frozen=True, slots=True)
class NonEscapingValue:
    value: Any
    owner: str
    escaped: bool = False

    def publish(self) -> Any:
        if self.escaped:
            raise CapabilityViolation("local preparation effect escaped")
        return self.value


def assert_non_escaping(value: NonEscapingValue) -> None:
    if not isinstance(value, NonEscapingValue) or value.escaped:
        raise CapabilityViolation("local preparation effect escaped")
