"""Small, explicit V6.1 scheduler policy surface."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, ClassVar


@dataclass(frozen=True, slots=True)
class V61Policy:
    """Bounded JIT policy.

    ``lookahead`` counts sources beyond the source currently awaiting
    publication. ``future_cap`` limits future provider calls, and
    ``native_future_quota`` is the maximum already-active future calls that a
    native interval may tolerate.
    """

    lookahead: int = 1
    future_cap: int = 1
    native_future_quota: int = 0

    # Deployment facts from the fixed local vLLM profile.  The server reports
    # 65,968 KV-cache tokens; reserve 4,528 tokens for scheduler/block
    # granularity and output-estimation error.  These values describe the
    # system resource envelope and are not autoresearch knobs.
    LOCAL_KV_CACHE_TOKENS: ClassVar[int] = 65_968
    KV_HEADROOM_TOKENS: ClassVar[int] = 4_528
    MAX_ADMITTED_KV_TOKENS: ClassVar[int] = (
        LOCAL_KV_CACHE_TOKENS - KV_HEADROOM_TOKENS
    )

    # Structured Graphiti responses in the characterized workload stay below
    # 2K tokens.  Reserve twice that amount per active request.  Admission is
    # based on prompt residency plus this decode credit, rather than treating
    # the API's 16K correctness ceiling as immediately resident KV state.
    STRUCTURED_DECODE_RESERVE_TOKENS: ClassVar[int] = 4_096

    def __post_init__(self) -> None:
        values = (self.lookahead, self.future_cap, self.native_future_quota)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
            raise ValueError("V6.1 policy values must be integers")
        if self.lookahead < 1:
            raise ValueError("lookahead must be at least one")
        if not 0 <= self.native_future_quota <= self.future_cap <= 7:
            raise ValueError("policy must satisfy 0 <= native_future_quota <= future_cap <= 7")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "membind.v6.1.policy.v3",
            **asdict(self),
            "resource_model": {
                "kind": "prefill_plus_structured_decode_reserve",
                "local_kv_cache_tokens": self.LOCAL_KV_CACHE_TOKENS,
                "kv_headroom_tokens": self.KV_HEADROOM_TOKENS,
                "max_admitted_kv_tokens": self.MAX_ADMITTED_KV_TOKENS,
                "structured_decode_reserve_tokens": self.STRUCTURED_DECODE_RESERVE_TOKENS,
                "dimensions": ["provider_slots", "kv_tokens"],
            },
        }

    def token_budget(self, authority: int) -> int:
        """Return the weighted provider budget for the active runtime."""
        if isinstance(authority, bool) or not isinstance(authority, int) or authority <= 0:
            raise ValueError("authority must be a positive integer")
        return min(authority * 8_192, self.MAX_ADMITTED_KV_TOKENS)
