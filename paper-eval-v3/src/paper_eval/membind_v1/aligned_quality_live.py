"""Read-only session-retrieval and correctness observation for aligned blocks.

This isolated module runs only after a fresh aligned construction block has a
complete durable lifecycle.  It never receives Reader/Judge callbacks and
never writes the graph, namespace, or block root.  A caller injects two
read-only operations: formal session retrieval and a generic namespace
correctness observer.  The resulting content-safe projection is hash-bound to
the same plan block/manifest and can be passed directly to the explicit
quality builder in :mod:`aligned_metrics`.
"""

from __future__ import annotations

import asyncio
import inspect
import math
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v1.aligned_artifacts import inspect_aligned_block_artifacts
from paper_eval.membind_v1.aligned_metrics import (
    GRAPH_NATIVE_PROTOCOL_DEGENERATE,
    build_aligned_quality_and_correctness,
)
from paper_eval.membind_v1.aligned_plan import (
    ALIGNED_METHODS,
    verify_aligned_development_plan,
)
from paper_eval.s2_session_policy import evaluate_session_retrieval


SCHEMA = "membind.paper-eval-v3.membind-v1-aligned-quality-live.v1"
_TOP_K = 10


class AlignedQualityLiveError(RuntimeError):
    """A read-only quality observation or its frozen identity is invalid."""


def _fail(code: str) -> AlignedQualityLiveError:
    return AlignedQualityLiveError(code)


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise _fail(code)
    try:
        int(value, 16)
    except ValueError:
        raise _fail(code) from None
    return value


def _nonnegative_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value:
        raise _fail(code)
    return value


def _ids(value: object, code: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _fail(code)
    result = tuple(value)
    if (not allow_empty and not result) or any(
        not isinstance(item, str) or not item for item in result
    ) or len(set(result)) != len(result):
        raise _fail(code)
    return result


@dataclass(frozen=True, slots=True)
class SessionRetrievalRequest:
    """Gold-blind payload given to the caller's read-only retrieval callable."""

    question_sha256: str
    query: str

    def __post_init__(self) -> None:
        _sha(self.question_sha256, "retrieval question identity invalid")
        _text(self.query, "retrieval query invalid")


@dataclass(frozen=True, slots=True)
class SessionRetrievalCase:
    """Frozen local scoring data for one session retrieval question.

    ``gold_session_ids`` deliberately never cross the retrieval callback
    boundary.  They are used only after its ranked result returns.
    """

    question_sha256: str
    query: str
    gold_session_ids: tuple[str, ...]
    allowed_session_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _sha(self.question_sha256, "retrieval question identity invalid")
        _text(self.query, "retrieval query invalid")
        gold = _ids(self.gold_session_ids, "gold session identities invalid")
        allowed = _ids(self.allowed_session_ids, "allowed session identities invalid")
        if not set(gold).issubset(allowed):
            raise _fail("gold session identities escape allowed corpus")

    def request(self) -> SessionRetrievalRequest:
        return SessionRetrievalRequest(
            question_sha256=self.question_sha256,
            query=self.query,
        )


@dataclass(frozen=True, slots=True)
class NamespaceCorrectnessObservation:
    """Generic exact namespace observation, with no fixed history cardinality."""

    observed_source_sha256s: tuple[str, ...]
    namespace_escape_count: int

    def __post_init__(self) -> None:
        for item in self.observed_source_sha256s:
            _sha(item, "observed source identity invalid")
        _nonnegative_int(self.namespace_escape_count, "namespace escape count invalid")


ReadOnlySessionRetriever = Callable[
    ..., Awaitable[Sequence[str]]
]
NamespaceCorrectnessObserver = Callable[
    ..., Awaitable[NamespaceCorrectnessObservation]
]


@dataclass(frozen=True, slots=True)
class AlignedQualityLiveHooks:
    """Only read-only evaluation operations; Reader/Judge/write hooks absent."""

    retrieve_sessions: ReadOnlySessionRetriever
    observe_namespace_correctness: NamespaceCorrectnessObserver

    def __post_init__(self) -> None:
        if not callable(self.retrieve_sessions) or not callable(
            self.observe_namespace_correctness
        ):
            raise _fail("quality live hooks invalid")


def _plan_block(
    verified_plan: Mapping[str, object], block_index: object
) -> tuple[dict[str, Any], dict[str, object]]:
    try:
        plan = verify_aligned_development_plan(verified_plan)
    except ValueError:
        raise _fail("verified plan invalid") from None
    index = _nonnegative_int(block_index, "block index invalid")
    blocks = plan.get("blocks")
    if not isinstance(blocks, list) or index >= len(blocks):
        raise _fail("plan block invalid")
    block = blocks[index]
    if not isinstance(block, Mapping) or block.get("block_index") != index:
        raise _fail("plan block invalid")
    if block.get("method") not in ALIGNED_METHODS:
        raise _fail("plan block invalid")
    return plan, deepcopy(dict(block))


def _complete_manifest(
    root: Path, *, plan: Mapping[str, object], block: Mapping[str, object]
) -> dict[str, object]:
    try:
        inspected = inspect_aligned_block_artifacts(Path(root))
    except ValueError:
        raise _fail("aligned artifact invalid") from None
    manifest = inspected.get("manifest")
    checkpoint = inspected.get("checkpoint")
    if not isinstance(manifest, Mapping) or not isinstance(checkpoint, Mapping):
        raise _fail("aligned artifact invalid")
    expected = {
        "aligned_run_id": block["aligned_run_id"],
        "block_index": block["block_index"],
        "method": block["method"],
        "history_id": block["history_id"],
        "namespace": block["namespace"],
        "source_manifest_sha256": block["source_manifest_sha256"],
        "arrival_trace_sha256": block["arrival_trace_sha256"],
        "history_arrival_trace_sha256": block[
            "history_arrival_trace_sha256"
        ],
        "shared_execution_envelope_sha256": block[
            "shared_execution_envelope_sha256"
        ],
        "global_llm_admission_k": 2,
        "plan_payload_sha256": plan["payload_sha256"],
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise _fail("manifest plan block binding invalid")
    sources = plan["history_source_sha256s"].get(block["history_id"])
    if not isinstance(sources, list) or manifest.get("source_sha256s") != sources:
        raise _fail("manifest source coverage invalid")
    if checkpoint.get("terminal_status") != "COMPLETED" or checkpoint.get(
        "complete_coverage"
    ) is not True:
        raise _fail("complete coverage required")
    return dict(manifest)


def summarize_namespace_correctness(
    *,
    expected_source_sha256s: Sequence[str],
    observation: NamespaceCorrectnessObservation,
) -> dict[str, int]:
    """Compute exact source coverage violations for any history cardinality."""

    expected = tuple(
        _sha(item, "expected source identity invalid")
        for item in _ids(
            expected_source_sha256s, "expected source inventory invalid"
        )
    )
    if not isinstance(observation, NamespaceCorrectnessObservation):
        raise _fail("namespace correctness observation invalid")
    observed = tuple(observation.observed_source_sha256s)
    expected_counts = Counter(expected)
    observed_counts = Counter(observed)
    lost = sum(
        max(0, expected_counts[source] - observed_counts[source])
        for source in expected_counts
    )
    duplicate = sum(
        max(0, observed_counts[source] - expected_counts[source])
        for source in expected_counts
    )
    unexpected = sum(
        count for source, count in observed_counts.items() if source not in expected_counts
    )
    escaped = observation.namespace_escape_count
    return {
        "lost_episodic_count": lost,
        "duplicate_episodic_count": duplicate,
        "unexpected_episodic_count": unexpected,
        "episodic_namespace_escape_count": escaped,
        "direct_violations": lost + duplicate + unexpected + escaped,
    }


async def _await(value: object, code: str) -> object:
    if not inspect.isawaitable(value):
        raise _fail(code)
    return await value


async def _retrieve_case(
    *,
    namespace: str,
    case: SessionRetrievalCase,
    hooks: AlignedQualityLiveHooks,
) -> dict[str, object]:
    try:
        raw = await _await(
            hooks.retrieve_sessions(namespace=namespace, request=case.request()),
            "read-only retrieval must be async",
        )
    except asyncio.CancelledError:
        raise
    except AlignedQualityLiveError:
        raise
    except Exception:
        raise _fail("read-only retrieval failed") from None
    retrieved = _ids(raw, "read-only retrieval result invalid")
    if len(retrieved) != _TOP_K:
        raise _fail("read-only retrieval must return exactly top-10 sessions")
    try:
        metric = evaluate_session_retrieval(
            retrieved_session_ids=retrieved,
            gold_session_ids=case.gold_session_ids,
            top_k=_TOP_K,
            allowed_session_ids=case.allowed_session_ids,
        )
    except ValueError:
        raise _fail("read-only retrieval metric invalid") from None
    # These summaries are deliberately content-safe.  Raw query/gold/ranked
    # session values remain in process only and are never written by this module.
    return {
        "question_sha256": case.question_sha256,
        "retrieved_session_ids_sha256": payload_sha256(list(retrieved)),
        "gold_session_ids_sha256": payload_sha256(list(case.gold_session_ids)),
        "retrieved_session_count": metric.retrieved_session_count,
        "gold_session_count": metric.gold_session_count,
        "covered_gold_session_count": metric.covered_gold_session_count,
        "evidence_recall_at_10": metric.evidence_recall_at_10,
        "gold_ranks": list(metric.gold_ranks),
    }


async def observe_aligned_quality_live(
    root: Path,
    *,
    verified_plan: Mapping[str, object],
    block_index: int,
    retrieval_cases: Sequence[SessionRetrievalCase],
    hooks: AlignedQualityLiveHooks,
) -> dict[str, object]:
    """Perform one common, read-only quality observation after construction.

    No Reader/Judge argument exists.  QA is explicitly unmeasured and emitted
    as ``None`` under the established graph-native-degenerate status.
    """

    plan, block = _plan_block(verified_plan, block_index)
    manifest = _complete_manifest(Path(root), plan=plan, block=block)
    if not isinstance(hooks, AlignedQualityLiveHooks):
        raise _fail("quality live hooks invalid")
    if isinstance(retrieval_cases, (str, bytes)) or not isinstance(
        retrieval_cases, Sequence
    ):
        raise _fail("retrieval case inventory invalid")
    cases = tuple(retrieval_cases)
    if not cases or any(not isinstance(case, SessionRetrievalCase) for case in cases):
        raise _fail("retrieval case inventory invalid")
    if len({case.question_sha256 for case in cases}) != len(cases):
        raise _fail("retrieval case identity duplicate")

    namespace = _text(manifest.get("namespace"), "namespace invalid")
    retrieval_results = [
        await _retrieve_case(namespace=namespace, case=case, hooks=hooks)
        for case in cases
    ]
    try:
        observation = await _await(
            hooks.observe_namespace_correctness(
                namespace=namespace,
                expected_source_sha256s=tuple(manifest["source_sha256s"]),
            ),
            "namespace correctness observer must be async",
        )
    except asyncio.CancelledError:
        raise
    except AlignedQualityLiveError:
        raise
    except Exception:
        raise _fail("namespace correctness observation failed") from None
    if not isinstance(observation, NamespaceCorrectnessObservation):
        raise _fail("namespace correctness observation invalid")
    correctness = summarize_namespace_correctness(
        expected_source_sha256s=tuple(manifest["source_sha256s"]),
        observation=observation,
    )
    evidence_recall = sum(
        float(result["evidence_recall_at_10"]) for result in retrieval_results
    ) / len(retrieval_results)
    quality = build_aligned_quality_and_correctness(
        Path(root),
        verified_plan=plan,
        block_index=block_index,
        qa_accuracy=None,
        evidence_recall_at_10=evidence_recall,
        direct_violations=correctness["direct_violations"],
        quality_status=GRAPH_NATIVE_PROTOCOL_DEGENERATE,
    )
    body = {
        "schema_version": SCHEMA,
        "status": "PASS",
        "aligned_run_id": block["aligned_run_id"],
        "block_index": block["block_index"],
        "method": block["method"],
        "history_id": block["history_id"],
        "namespace": namespace,
        "source_manifest_sha256": block["source_manifest_sha256"],
        "arrival_trace_sha256": block["arrival_trace_sha256"],
        "shared_execution_envelope_sha256": block[
            "shared_execution_envelope_sha256"
        ],
        "global_llm_admission_k": 2,
        "manifest_sha256": manifest["manifest_sha256"],
        "execution_identity_sha256": manifest["execution_identity_sha256"],
        "retrieval_summary": {
            "case_count": len(retrieval_results),
            "evidence_recall_at_10": evidence_recall,
            "cases": retrieval_results,
        },
        "correctness_summary": correctness,
        "quality_and_correctness": quality,
    }
    return {**body, "payload_sha256": payload_sha256(body)}


__all__ = [
    "AlignedQualityLiveError",
    "AlignedQualityLiveHooks",
    "NamespaceCorrectnessObservation",
    "SCHEMA",
    "SessionRetrievalCase",
    "SessionRetrievalRequest",
    "observe_aligned_quality_live",
    "summarize_namespace_correctness",
]
