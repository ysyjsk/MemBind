"""Small content-safe telemetry recorder for v4 candidates."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from copy import deepcopy


class V4TelemetryError(ValueError):
    """Telemetry contains private content or malformed fields."""


_PRIVATE_KEYS = {
    "prompt",
    "prompt_text",
    "response",
    "response_text",
    "raw_prompt",
    "raw_response",
    "token_ids",
    "body",
    "content",
}


def _check_safe(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise V4TelemetryError("telemetry_key_invalid")
            if key.lower() in _PRIVATE_KEYS:
                raise V4TelemetryError("private_telemetry_field")
            _check_safe(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _check_safe(child)
    elif isinstance(value, (str, int, float, bool)) or value is None:
        return
    else:
        raise V4TelemetryError("telemetry_value_invalid")


class V4Telemetry:
    """Append-only in-memory telemetry suitable for candidate traces.

    A live writer can consume ``events`` after each call.  This class does not
    persist raw model inputs or responses and does not perform blocking I/O.
    """

    def __init__(self) -> None:
        self._events: list[dict[str, object]] = []

    def record(self, event_type: str, **fields: object) -> dict[str, object]:
        if not isinstance(event_type, str) or not event_type:
            raise V4TelemetryError("event_type_invalid")
        _check_safe(fields)
        event: dict[str, object] = {
            "event_sequence": len(self._events),
            "event_type": event_type,
            **deepcopy(fields),
        }
        self._events.append(event)
        return dict(event)

    def record_llm(
        self,
        *,
        request_id: str,
        request_kind: str,
        source_sequence: int,
        prompt_tokens: int,
        output_tokens: int,
        service_span_ns: int,
        status: str = "OK",
        retry_count: int = 0,
    ) -> dict[str, object]:
        return self.record(
            "llm",
            request_id=request_id,
            request_kind=request_kind,
            source_sequence=source_sequence,
            prompt_tokens=prompt_tokens,
            output_tokens=output_tokens,
            service_span_ns=service_span_ns,
            status=status,
            retry_count=retry_count,
        )

    @property
    def events(self) -> tuple[dict[str, object], ...]:
        return tuple(deepcopy(event) for event in self._events)

    def summary(self) -> dict[str, object]:
        counts = Counter(str(event["event_type"]) for event in self._events)
        hits = counts.get("semantic_hit", 0)
        misses = counts.get("semantic_miss", 0)
        qualified = hits + misses
        return {
            "schema_version": "membind.paper-eval-v4.telemetry-summary.v1",
            "event_count": len(self._events),
            "event_counts": dict(counts),
            "qualified_node_resolve_count": qualified,
            "semantic_hit_count": hits,
            "semantic_miss_count": misses,
            "semantic_hit_rate": hits / qualified if qualified else None,
        }


__all__ = ["V4Telemetry", "V4TelemetryError"]
