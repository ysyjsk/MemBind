"""One-build-many-QA runner with read-only and resume enforcement."""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from .artifacts import ArtifactStore
from .compatibility import session_ranking_metrics
from .contracts import (
    MABQA,
    MABContext,
    PublicContext,
    assert_gold_blind,
    canonical_sha256,
)


class QAWriteViolation(RuntimeError):
    """A QA-phase callback attempted to mutate the sealed namespace."""


class NamespaceNotSealedError(RuntimeError):
    """Construction returned without a sealed namespace."""


class ResumeIdentityMismatch(RuntimeError):
    """Existing construction or QA artifacts belong to another identity."""


class ConstructionMethod(Protocol):
    method_id: str
    implementation_sha256: str


@dataclass(frozen=True)
class ConstructionReceipt:
    method: str
    context_id: str
    namespace: str
    context_sha256: str
    construction_manifest_sha256: str
    namespace_sealed: bool
    construction_count: int
    episode_uuid_to_session_id: dict[str, str]
    identity_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "mab-quality-v2-final-qa.construction-receipt.v1",
            "method": self.method,
            "context_id": self.context_id,
            "namespace": self.namespace,
            "context_sha256": self.context_sha256,
            "construction_manifest_sha256": self.construction_manifest_sha256,
            "namespace_sealed": self.namespace_sealed,
            "construction_count": self.construction_count,
            "episode_uuid_to_session_id": dict(self.episode_uuid_to_session_id),
            "identity_sha256": self.identity_sha256,
        }


@dataclass(frozen=True)
class QAResult:
    method: str
    context_id: str
    qa_pair_id: str
    question_id: str
    question_type: str
    status: str
    failure_class: str | None
    judge_valid: bool
    correct: bool | None
    retrieval_metrics: dict[str, Any]
    answer: str | None
    namespace: str
    context_sha256: str
    construction_manifest_sha256: str
    attempt: int
    identity_sha256: str
    qa_identity_sha256: str

    def as_dict(self) -> dict[str, Any]:
        body = {
            "schema_version": "mab-quality-v2-final-qa.qa-row.v1",
            "method": self.method,
            "context_id": self.context_id,
            "qa_pair_id": self.qa_pair_id,
            "question_id": self.question_id,
            "question_type": self.question_type,
            "status": self.status,
            "failure_class": self.failure_class,
            "judge_valid": self.judge_valid,
            "correct": self.correct,
            "retrieval_metrics": dict(self.retrieval_metrics),
            "answer": self.answer,
            "namespace": self.namespace,
            "context_sha256": self.context_sha256,
            "construction_manifest_sha256": self.construction_manifest_sha256,
            "attempt": self.attempt,
            "identity_sha256": self.identity_sha256,
            "qa_identity_sha256": self.qa_identity_sha256,
        }
        body["payload_sha256"] = canonical_sha256(body)
        return body


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.=-]+", "_", value)[:160]


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class ConstructionWriter:
    """Small construction-only facade; its methods are unavailable after seal."""

    def __init__(self, graph: Any, namespace: str) -> None:
        self._graph = graph
        self.namespace = namespace
        self._sealed = False
        self.write_count = 0

    def _check(self) -> None:
        if self._sealed:
            raise QAWriteViolation("QA_PHASE_WRITE_VIOLATION")

    async def add_episode(self, *args: Any, **kwargs: Any) -> Any:
        self._check()
        function = getattr(self._graph, "add_episode", None)
        if not callable(function):
            raise TypeError("construction graph does not expose add_episode")
        self.write_count += 1
        return await _maybe_await(function(*args, **kwargs))

    def seal(self) -> None:
        self._sealed = True


class ReadOnlyNamespace:
    """Proxy that converts common Graphiti writes into a hard QA failure."""

    _MUTATIONS = frozenset(
        {
            "add_episode",
            "add_node",
            "add_fact",
            "delete_episode",
            "delete_node",
            "delete_group",
            "clear",
            "write",
            "update",
            "merge",
            "remove",
        }
    )

    def __init__(self, graph: Any, namespace: str) -> None:
        self._graph = graph
        self.namespace = namespace

    def __getattr__(self, name: str) -> Any:
        if name in self._MUTATIONS or name.startswith(("add_", "delete_")):

            def forbidden(*_args: Any, **_kwargs: Any) -> None:
                raise QAWriteViolation("QA_PHASE_WRITE_VIOLATION")

            return forbidden
        return getattr(self._graph, name)


def _bundle(value: Any) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    facts = getattr(value, "facts", None)
    episodes = getattr(value, "episodes", None)
    if isinstance(value, Mapping):
        facts = value.get("facts", facts)
        episodes = value.get("episodes", episodes)
    if facts is None:
        facts = ()
    if episodes is None:
        episodes = ()
    if isinstance(facts, (str, bytes)) or isinstance(episodes, (str, bytes)):
        raise TypeError("retrieval bundle sequences are invalid")
    return tuple(facts), tuple(episodes)


def _episode_session_ids(
    episodes: Sequence[Any], mapping: Mapping[str, str]
) -> list[str]:
    result: list[str] = []
    for episode in episodes:
        session = getattr(episode, "session_id", None)
        uuid = getattr(episode, "episode_uuid", None)
        if isinstance(episode, Mapping):
            session = episode.get("session_id", session)
            uuid = episode.get("episode_uuid", episode.get("uuid", uuid))
        if session is None and uuid is not None:
            session = mapping.get(str(uuid))
        if not isinstance(session, str) or not session:
            raise ValueError("retrieval episode has no mapped session")
        if session in result:
            raise ValueError("retrieval returned duplicate session")
        result.append(session)
    return result


def _judge(value: Any) -> tuple[bool, bool | None, str | None]:
    if isinstance(value, bool):
        return True, value, None
    if not isinstance(value, Mapping):
        raise TypeError("judge result must be bool or {valid, correct}")
    valid = value.get("valid")
    correct = value.get("correct")
    if not isinstance(valid, bool):
        raise TypeError("judge.valid must be bool")
    if valid and not isinstance(correct, bool):
        raise ValueError("valid judge.correct must be bool")
    return (
        valid,
        correct if valid else None,
        str(value.get("failure_class")) if value.get("failure_class") else None,
    )


class MABQualityRunner:
    """Execute a context exactly once, then run/resume its QA inventory."""

    def __init__(
        self,
        *,
        store: ArtifactStore,
        method: Any,
        run_id: str,
        dataset_manifest_sha256: str,
        graph: Any,
        construct: Callable[..., Any],
        retrieve: Callable[..., Any],
        reader: Callable[..., Any],
        judge: Callable[..., Any],
        context_pack: Callable[..., Any],
        metrics: Callable[..., Any] = session_ranking_metrics,
        reader_config_sha256: str = "UNBOUND",
        judge_config_sha256: str = "UNBOUND",
        retrieval_config_sha256: str = "UNBOUND",
        namespace_validator: Callable[[ConstructionReceipt], Any] | None = None,
    ) -> None:
        self.store = store
        self.method = method
        self.method_id = str(getattr(method, "method_id", "UNKNOWN"))
        self.implementation_sha256 = str(
            getattr(method, "implementation_sha256", "UNBOUND")
        )
        self.run_id = run_id
        self.dataset_manifest_sha256 = dataset_manifest_sha256
        self.graph = graph
        self.construct = construct
        self.retrieve = retrieve
        self.reader = reader
        self.judge = judge
        self.context_pack = context_pack
        self.metrics = metrics
        self.namespace_validator = namespace_validator
        self.identity_sha256 = canonical_sha256(
            {
                "schema": "mab-quality-v2-final-qa.runner.v1",
                "run_id": run_id,
                "method": self.method_id,
                "implementation_sha256": self.implementation_sha256,
                "dataset_manifest_sha256": dataset_manifest_sha256,
                "retrieval_config_sha256": retrieval_config_sha256,
                "reader_config_sha256": reader_config_sha256,
                "judge_config_sha256": judge_config_sha256,
            }
        )

    def _namespace(self, context: MABContext) -> str:
        return f"pev3-mabqv2final-{_safe(self.run_id)}-{_safe(self.method_id)}-{_safe(context.context_id)}"

    def _receipt_relative(self, context: MABContext) -> str:
        return f"construction/{_safe(self.method_id)}/{_safe(context.context_id)}/receipt.json"

    def _qa_relative(self, context: MABContext) -> str:
        return f"qa/{_safe(self.method_id)}/{_safe(context.context_id)}/attempts.jsonl"

    def _load_receipt(self, context: MABContext) -> ConstructionReceipt | None:
        path = self.store.path(self._receipt_relative(context))
        if not path.exists():
            return None
        import json

        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ResumeIdentityMismatch(
                "construction receipt is unreadable"
            ) from error
        if (
            body.get("context_sha256") != context.context_sha256
            or body.get("method") != self.method_id
            or body.get("identity_sha256") != self.identity_sha256
            or body.get("construction_count") != 1
            or body.get("namespace_sealed") is not True
        ):
            raise ResumeIdentityMismatch("construction receipt identity mismatch")
        return ConstructionReceipt(
            method=body["method"],
            context_id=body["context_id"],
            namespace=body["namespace"],
            context_sha256=body["context_sha256"],
            construction_manifest_sha256=body["construction_manifest_sha256"],
            namespace_sealed=True,
            construction_count=1,
            episode_uuid_to_session_id=dict(body.get("episode_uuid_to_session_id", {})),
            identity_sha256=body["identity_sha256"],
        )

    async def _construct_once(self, context: MABContext) -> ConstructionReceipt:
        existing = self._load_receipt(context)
        if existing is not None:
            if self.namespace_validator is not None:
                valid = await _maybe_await(self.namespace_validator(existing))
                if valid is not True:
                    raise ResumeIdentityMismatch(
                        "sealed namespace validation failed"
                    )
            return existing
        namespace = self._namespace(context)
        public = context.public_context()
        assert_gold_blind(public.as_dict())
        writer = ConstructionWriter(self.graph, namespace)
        try:
            value = await _maybe_await(
                self.construct(
                    public_context=public.as_dict(), namespace=namespace, writer=writer
                )
            )
        except QAWriteViolation:
            raise
        except Exception as error:
            raise RuntimeError("CONSTRUCTION_FAILED") from error
        if isinstance(value, Mapping):
            if value.get("namespace_sealed") is not True:
                raise NamespaceNotSealedError("NAMESPACE_NOT_SEALED")
            mapping = value.get("episode_uuid_to_session_id", {})
            manifest = value.get("construction_manifest_sha256")
        else:
            mapping = {}
            manifest = None
        if mapping is None or not isinstance(mapping, Mapping):
            raise RuntimeError("CONSTRUCTION_FAILED")
        writer.seal()
        manifest_hash = str(
            manifest
            or canonical_sha256(
                {"context": context.context_sha256, "writes": writer.write_count}
            )
        )
        receipt = ConstructionReceipt(
            method=self.method_id,
            context_id=context.context_id,
            namespace=namespace,
            context_sha256=context.context_sha256,
            construction_manifest_sha256=manifest_hash,
            namespace_sealed=True,
            construction_count=1,
            episode_uuid_to_session_id={str(k): str(v) for k, v in mapping.items()},
            identity_sha256=self.identity_sha256,
        )
        self.store.write_json(self._receipt_relative(context), receipt.as_dict())
        return receipt

    def _latest_attempts(self, context: MABContext) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for row in self.store.read_jsonl(self._qa_relative(context)):
            stored_hash = row.get("payload_sha256")
            if stored_hash != canonical_sha256(
                {key: value for key, value in row.items() if key != "payload_sha256"}
            ):
                raise ResumeIdentityMismatch("QA row payload hash mismatch")
            key = (row.get("context_id"), row.get("qa_pair_id"))
            if key[0] != context.context_id or not isinstance(key[1], str):
                raise ResumeIdentityMismatch("QA row identity mismatch")
            if row.get("identity_sha256") != self.identity_sha256:
                raise ResumeIdentityMismatch("QA row runner identity mismatch")
            old = latest.get(key[1])
            if old is None or int(row.get("attempt", 0)) > int(old.get("attempt", 0)):
                latest[key[1]] = row
        return latest

    async def run_context(self, context: MABContext) -> tuple[dict[str, Any], ...]:
        receipt = await self._construct_once(context)
        latest = self._latest_attempts(context)
        readonly = ReadOnlyNamespace(self.graph, receipt.namespace)
        public_context = context.public_context()
        output: list[dict[str, Any]] = []
        for qa in context.qa_items:
            prior = latest.get(qa.qa_pair_id)
            if (
                prior is not None
                and prior.get("status") == "COMPLETE"
                and prior.get("judge_valid") is True
            ):
                output.append(prior)
                continue
            attempt = int(prior.get("attempt", 0)) + 1 if prior else 1
            row = await self._run_qa(
                context=context,
                qa=qa,
                receipt=receipt,
                public_context=public_context,
                readonly=readonly,
                attempt=attempt,
            )
            self.store.append_jsonl(self._qa_relative(context), row)
            latest[qa.qa_pair_id] = row
            output.append(row)
        return tuple(output)

    async def _run_qa(
        self,
        *,
        context: MABContext,
        qa: MABQA,
        receipt: ConstructionReceipt,
        public_context: PublicContext,
        readonly: ReadOnlyNamespace,
        attempt: int,
    ) -> dict[str, Any]:
        base = {
            "method": self.method_id,
            "context_id": context.context_id,
            "qa_pair_id": qa.qa_pair_id,
            "question_id": qa.question_id,
            "question_type": qa.question_type,
            "namespace": receipt.namespace,
            "context_sha256": context.context_sha256,
            "construction_manifest_sha256": receipt.construction_manifest_sha256,
            "attempt": attempt,
            "identity_sha256": self.identity_sha256,
            "qa_identity_sha256": canonical_sha256(qa.public_dict()),
            "retrieval_metrics": {},
            "answer": None,
            "judge_valid": False,
            "correct": None,
        }
        public_qa = qa.public_dict()
        assert_gold_blind(public_qa)
        try:
            bundle = await _maybe_await(
                self.retrieve(
                    query=qa.question,
                    public_qa=public_qa,
                    namespace=receipt.namespace,
                    graph=readonly,
                    episode_uuid_to_session_id=dict(receipt.episode_uuid_to_session_id),
                )
            )
            facts, episodes = _bundle(bundle)
            ranked = _episode_session_ids(episodes, receipt.episode_uuid_to_session_id)
            metrics = dict(self.metrics(ranked, qa.gold_session_ids))
            base["retrieval_metrics"] = metrics
            pack = await _maybe_await(
                self.context_pack(
                    context=public_context,
                    question=qa.question,
                    facts=facts,
                    episodes=episodes,
                )
            )
            context_json = getattr(pack, "context_json", None)
            if isinstance(pack, Mapping):
                context_json = pack.get("context_json", context_json)
            if not isinstance(context_json, str) or not context_json.strip():
                raise ValueError("CONTEXT_PACK_INVALID")
            try:
                import json

                decoded = json.loads(context_json)
            except (TypeError, json.JSONDecodeError) as error:
                raise ValueError("CONTEXT_PACK_INVALID") from error
            assert_gold_blind(decoded)
            answer_value = await _maybe_await(
                self.reader(
                    context_json=context_json,
                    question=qa.question,
                    question_date=qa.question_date,
                    public_qa=public_qa,
                )
            )
            if isinstance(answer_value, Mapping):
                answer = answer_value.get("answer", answer_value.get("content"))
            else:
                answer = answer_value
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError("READER_FAILED")
            base["answer"] = answer.strip()
            valid, correct, judge_failure = _judge(
                await _maybe_await(
                    self.judge(
                        labels=qa.private_labels(),
                        answer=base["answer"],
                        public_qa=public_qa,
                    )
                )
            )
            base["judge_valid"] = valid
            base["correct"] = correct
            if not valid:
                base["status"] = "INVALID"
                base["failure_class"] = judge_failure or "JUDGE_INVALID"
            else:
                base["status"] = "COMPLETE"
                base["failure_class"] = None
        except QAWriteViolation:
            base["status"] = "INVALID"
            base["failure_class"] = "QA_PHASE_WRITE_VIOLATION"
        except ValueError as error:
            message = str(error)
            known = {
                "RETRIEVAL_FAILED",
                "CONTEXT_PACK_INVALID",
                "READER_FAILED",
                "READER_INVALID_FINISH",
                "JUDGE_FAILED",
                "JUDGE_INVALID",
                "GOLD_LEAK_DETECTED",
            }
            base["status"] = "INVALID"
            base["failure_class"] = (
                message if message in known else "UNKNOWN_INFRA_FAILURE"
            )
        # Unknown callback/provider failures remain invalid infrastructure rows.
        except Exception:  # noqa: BLE001
            base["status"] = "INVALID"
            base["failure_class"] = "UNKNOWN_INFRA_FAILURE"
        base["payload_sha256"] = canonical_sha256(base)
        return base

    async def run_many(
        self, contexts: Sequence[MABContext]
    ) -> tuple[dict[str, Any], ...]:
        rows: list[dict[str, Any]] = []
        for context in contexts:
            rows.extend(await self.run_context(context))
        return tuple(rows)


__all__ = [
    "ConstructionReceipt",
    "ConstructionWriter",
    "MABQualityRunner",
    "NamespaceNotSealedError",
    "QAResult",
    "QAWriteViolation",
    "ReadOnlyNamespace",
    "ResumeIdentityMismatch",
]
