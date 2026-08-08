"""Safe runtime audit for cross-encoder calls outside the frozen model oracle."""

from __future__ import annotations

import contextvars
import hashlib
import json
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from graphiti_core.cross_encoder.client import CrossEncoderClient
from instrumentation import current_episode_key


AUDIT_SCHEMA_VERSION = "membind.model_oracle_audit.v1"
_PHASE = contextvars.ContextVar("membind_model_oracle_phase", default="unscoped")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _utf8(value: str) -> bytes:
    if not isinstance(value, str):
        raise TypeError("cross-encoder inputs must be strings")
    return value.encode("utf-8")


@contextmanager
def model_oracle_phase(name: str) -> Iterator[None]:
    token = _PHASE.set(str(name))
    try:
        yield
    finally:
        _PHASE.reset(token)


class CrossEncoderAuditWrapper(CrossEncoderClient):
    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.config = getattr(inner, "config", None)
        self.rank_call_count = 0
        self.rank_events: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def rank(
        self,
        query: str,
        passages: list[str],
    ) -> list[tuple[str, float]]:
        query_bytes = _utf8(query)
        passage_bytes = [_utf8(passage) for passage in passages]
        episode_key = current_episode_key()
        event = {
            "ordinal": self.rank_call_count,
            "phase": _PHASE.get(),
            "episode_key": list(episode_key) if episode_key is not None else None,
            "query_sha256": _sha256(query_bytes),
            "query_length": len(query),
            "query_byte_length": len(query_bytes),
            "passage_count": len(passages),
            "passage_sha256": [_sha256(value) for value in passage_bytes],
            "passage_lengths": [len(value) for value in passages],
            "passage_byte_lengths": [len(value) for value in passage_bytes],
            "combined_input_sha256": _sha256(
                json.dumps(
                    {
                        "query_sha256": _sha256(query_bytes),
                        "passage_sha256": [_sha256(value) for value in passage_bytes],
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("ascii")
            ),
            "outcome": "pending",
        }
        self.rank_call_count += 1
        self.rank_events.append(event)
        try:
            result = await self.inner.rank(query, passages)
        except BaseException:
            event["outcome"] = "raised"
            raise
        event["outcome"] = "completed"
        return result


def model_oracle_audit_payload(
    cross_encoder: Any,
    *,
    run_id: str,
) -> dict[str, Any]:
    if not isinstance(cross_encoder, CrossEncoderAuditWrapper):
        raise ValueError("cross encoder is not instrumented for model-oracle audit")
    events = list(getattr(cross_encoder, "rank_events", []) or [])
    count = int(getattr(cross_encoder, "rank_call_count", 0))
    if count != len(events):
        raise ValueError("cross-encoder audit count does not match event count")
    phase_counts = Counter(str(event.get("phase")) for event in events)
    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "run_id": str(run_id),
        "measurement_scope": ["warmup", "construction", "final_retrieval"],
        "rank_call_count": count,
        "phase_call_counts": dict(sorted(phase_counts.items())),
        "cross_encoder_status": (
            "not_invoked" if count == 0 else "invoked_requires_capture_replay"
        ),
        "blocks_v2": count > 0,
        "events": events,
    }


def write_model_oracle_audit(
    cross_encoder: Any,
    output: str | Path,
    *,
    run_id: str,
) -> dict[str, Any]:
    output = Path(output)
    payload = model_oracle_audit_payload(cross_encoder, run_id=run_id)
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="ascii", newline="\n") as handle:
        handle.write(encoded)
    return payload
