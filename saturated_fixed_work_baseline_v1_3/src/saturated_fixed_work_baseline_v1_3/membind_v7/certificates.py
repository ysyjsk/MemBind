"""Fail-closed semantic read certificates (T3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from .state_delta import StateDelta


class CertificateStatus(str, Enum):
    STABLE = "STABLE"
    INVALID = "INVALID"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class Witness:
    operator: str
    query: Any
    result: tuple[str, ...]
    domain: tuple[str, ...]
    k: int
    cutoff: float | None
    ties: tuple[str, ...]
    query_epoch: str | None
    index_epoch: str | None
    filter_fingerprint: str | None = None
    ranking_fingerprint: str | None = None
    projection_fingerprint: str | None = None
    proof_data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.k <= 0:
            raise ValueError("witness k must be positive")
        object.__setattr__(self, "result", tuple(self.result))
        object.__setattr__(self, "domain", tuple(self.domain))
        object.__setattr__(self, "ties", tuple(self.ties))


@dataclass(frozen=True, slots=True)
class CertificateResult:
    status: CertificateStatus
    reason: str
    invalid_keys: tuple[str, ...] = ()


def _changed_relevant(witness: Witness, delta: StateDelta) -> tuple[Any, ...]:
    domain = set(witness.domain)
    return tuple(change for change in delta.changes if change.key in domain)


def certify_exact_topk(witness: Witness, delta: StateDelta) -> CertificateResult:
    """Guard exact full-scan cosine top-k, including short-result and tie cases."""

    if witness.operator not in {"node_cosine", "edge_cosine"}:
        return CertificateResult(CertificateStatus.UNKNOWN, "operator is not exact cosine")
    if not witness.query_epoch or not witness.index_epoch:
        return CertificateResult(CertificateStatus.UNKNOWN, "query/index epoch is missing")
    changed = _changed_relevant(witness, delta)
    if not changed:
        return CertificateResult(CertificateStatus.STABLE, "no domain observable changed")
    if witness.ties:
        return CertificateResult(CertificateStatus.UNKNOWN, "consumer-visible tie order has no contract")
    if len(witness.result) < witness.k or witness.cutoff is None:
        if witness.proof_data.get("no_new_eligible") is True and witness.proof_data.get("tie_contract"):
            return CertificateResult(CertificateStatus.STABLE, "explicit short-result exclusion proof")
        return CertificateResult(CertificateStatus.UNKNOWN, "short result has no kth cutoff")
    invalid = tuple(change.key for change in changed if change.key in witness.result)
    if invalid:
        return CertificateResult(CertificateStatus.INVALID, "result member changed", invalid)
    scores = witness.proof_data.get("post_scores", {})
    if isinstance(scores, Mapping) and all(key in scores and float(scores[key]) < float(witness.cutoff) for key in (change.key for change in changed)):
        if witness.proof_data.get("tie_contract"):
            return CertificateResult(CertificateStatus.STABLE, "post-delta score bounds remain below cutoff")
    # Without a post-delta score bound, an updated non-member may cross the
    # cutoff. Do not infer safety from a set comparison.
    return CertificateResult(CertificateStatus.UNKNOWN, "non-member score bound is unavailable")


def certify_exact_key(witness: Witness, delta: StateDelta) -> CertificateResult:
    if witness.operator not in {"node_key", "edge_key"}:
        return CertificateResult(CertificateStatus.UNKNOWN, "operator is not exact key")
    changed = _changed_relevant(witness, delta)
    if not changed:
        return CertificateResult(CertificateStatus.STABLE, "no key-domain mutation")
    invalid = tuple(change.key for change in changed if change.key in witness.result)
    return CertificateResult(CertificateStatus.INVALID, "key-domain mutation", invalid) if invalid else CertificateResult(CertificateStatus.STABLE, "key is absent from delta domain")


def certify_bm25(witness: Witness, delta: StateDelta, *, contract: dict[str, Any] | None = None) -> CertificateResult:
    if not contract or not contract.get("index_epoch") or not contract.get("stats_epoch") or not contract.get("tie_contract"):
        return CertificateResult(CertificateStatus.UNKNOWN, "BM25 index/statistics/tie contract unavailable")
    return CertificateResult(CertificateStatus.UNKNOWN, "BM25 certificate requires a backend score-bound proof")


def certify_hybrid(witness: Witness, delta: StateDelta, *, channel_contracts: dict[str, Any] | None = None) -> CertificateResult:
    if not channel_contracts or not all(channel_contracts.values()):
        return CertificateResult(CertificateStatus.UNKNOWN, "hybrid channel contract is incomplete")
    return CertificateResult(CertificateStatus.UNKNOWN, "RRF tie/order proof unavailable")


__all__ = [
    "CertificateResult",
    "CertificateStatus",
    "Witness",
    "certify_bm25",
    "certify_exact_key",
    "certify_exact_topk",
    "certify_hybrid",
]
