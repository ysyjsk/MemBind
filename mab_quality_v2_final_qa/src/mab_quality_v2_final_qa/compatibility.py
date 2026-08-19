"""Read-only projection into the existing Quality Evaluation v1 surface."""

from __future__ import annotations

import importlib
from collections.abc import Sequence
from typing import Any

from .contracts import MABContext, PublicContext, assert_gold_blind, canonical_sha256


class QualityV1CompatibilityError(ValueError):
    """Quality-v1 cannot consume the MAB projection without mutation."""


def to_quality_v1_record(context: MABContext | PublicContext) -> dict[str, Any]:
    """Return the exact aligned corpus shape expected by ``build_context_pack``."""

    if isinstance(context, MABContext):
        public = context.public_context()
    elif isinstance(context, PublicContext):
        public = context
    else:
        raise QualityV1CompatibilityError("context must be MABContext or PublicContext")
    sessions = sorted(public.sessions, key=lambda item: int(item["source_sequence"]))
    ids = [str(item["session_id"]) for item in sessions]
    dates = [str(item["timestamp"]) for item in sessions]
    turns = [[dict(turn) for turn in item["turns"]] for item in sessions]
    if not ids or len(ids) != len(dates) or len(ids) != len(turns):
        raise QualityV1CompatibilityError("session/date/turn alignment is invalid")
    if len(set(ids)) != len(ids) or any(not value for value in ids + dates):
        raise QualityV1CompatibilityError("Quality-v1 session identity is invalid")
    if any(
        not isinstance(turn, dict)
        or turn.get("role") not in {"user", "assistant"}
        or not isinstance(turn.get("content"), str)
        or not turn["content"]
        for session in turns
        for turn in session
    ):
        raise QualityV1CompatibilityError("Quality-v1 turn projection is invalid")
    record = {
        "haystack_session_ids": ids,
        "haystack_dates": dates,
        "haystack_sessions": turns,
    }
    assert_gold_blind(record)
    return record


def episode_uuid_to_session_id(context: MABContext | PublicContext) -> dict[str, str]:
    """Build a caller-owned Graphiti provenance map without labels."""

    public = context.public_context() if isinstance(context, MABContext) else context
    return {
        str(item["session_id"]): str(item["session_id"]) for item in public.sessions
    }


def quality_v1_identity() -> dict[str, str]:
    """Return hashes of the read-only Quality-v1 policies when importable."""

    try:
        module = importlib.import_module("paper_eval.quality_evaluation_v1")
        retrieval = importlib.import_module(
            "paper_eval.quality_evaluation_v1_retrieval"
        )
        reader = importlib.import_module("paper_eval.quality_evaluation_v1_reader")
    except (ImportError, ModuleNotFoundError) as error:
        raise QualityV1CompatibilityError(
            "existing Quality-v1 modules are unavailable; no replacement implementation is used"
        ) from error
    return {
        "context_policy_sha256": str(
            getattr(module, "CONTEXT_POLICY_SHA256", "UNAVAILABLE")
        ),
        "retrieval_module": canonical_sha256({"module": retrieval.__name__}),
        "reader_module": canonical_sha256({"module": reader.__name__}),
    }


def build_context_pack(
    *,
    context: MABContext | PublicContext,
    question: str,
    facts: Sequence[Any],
    episodes: Sequence[Any],
    quality_module: Any | None = None,
) -> Any:
    """Call the existing ``build_context_pack`` without changing it."""

    module = quality_module
    if module is None:
        try:
            module = importlib.import_module("paper_eval.quality_evaluation_v1")
        except (ImportError, ModuleNotFoundError) as error:
            raise QualityV1CompatibilityError(
                "Quality-v1 ContextPack module is unavailable"
            ) from error
    function = getattr(module, "build_context_pack", None)
    if not callable(function):
        raise QualityV1CompatibilityError(
            "Quality-v1 build_context_pack is unavailable"
        )
    return function(
        record=to_quality_v1_record(context),
        question=question,
        facts=tuple(facts),
        episodes=tuple(episodes),
    )


def session_ranking_metrics(
    ranked_session_ids: Sequence[str],
    gold_session_ids: Sequence[str],
    *,
    quality_module: Any | None = None,
) -> dict[str, Any]:
    """Delegate session metrics to frozen Quality-v1 code."""

    module = quality_module
    if module is None:
        try:
            module = importlib.import_module("paper_eval.quality_evaluation_v1")
        except (ImportError, ModuleNotFoundError) as error:
            raise QualityV1CompatibilityError(
                "Quality-v1 metrics module is unavailable"
            ) from error
    function = getattr(module, "session_ranking_metrics", None)
    if not callable(function):
        raise QualityV1CompatibilityError(
            "Quality-v1 session_ranking_metrics is unavailable"
        )
    return dict(function(ranked_session_ids, gold_session_ids))


__all__ = [
    "QualityV1CompatibilityError",
    "build_context_pack",
    "episode_uuid_to_session_id",
    "quality_v1_identity",
    "session_ranking_metrics",
    "to_quality_v1_record",
]
