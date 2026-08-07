"""Prompt/response cache used by correctness replay."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from graphiti_core.llm_client.client import LLMClient
from structured_output import constrain_single_episode_indices


_DIAGNOSTIC_TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)


class UnexpectedPromptError(RuntimeError):
    def __init__(self, prompt_hash: str, diagnostic: dict[str, Any] | None = None):
        super().__init__(f"unexpected prompt during read-only replay: {prompt_hash}")
        self.prompt_hash = prompt_hash
        self.diagnostic = diagnostic


@dataclass(frozen=True)
class PromptParts:
    model_revision: str
    decoding_config: dict[str, Any]
    structured_output_schema: dict[str, Any]
    system_prompt: str
    user_prompt: str


@dataclass(frozen=True)
class PromptRecord:
    prompt_hash: str
    raw_response: str
    parsed_response: Any
    token_usage: dict[str, Any]
    prompt_parts: dict[str, Any] = field(default_factory=dict)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def compute_prompt_hash(parts: PromptParts) -> str:
    payload = {
        "model_revision": parts.model_revision,
        "decoding_config": parts.decoding_config,
        "structured_output_schema": parts.structured_output_schema,
        "system_prompt": parts.system_prompt,
        "user_prompt": parts.user_prompt,
    }
    return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


def _component_hashes(parts: PromptParts | dict[str, Any]) -> dict[str, str]:
    values = asdict(parts) if isinstance(parts, PromptParts) else parts
    return {
        name: hashlib.sha256(_canonical_json(values.get(name)).encode()).hexdigest()
        for name in (
            "model_revision",
            "decoding_config",
            "structured_output_schema",
            "system_prompt",
            "user_prompt",
        )
    }


def _prompt_name(parts: PromptParts | dict[str, Any]) -> str | None:
    values = asdict(parts) if isinstance(parts, PromptParts) else parts
    config = values.get("decoding_config", {})
    if not isinstance(config, dict):
        return None
    value = config.get("prompt_name")
    return str(value) if value is not None else None


def _token_multiset(text: str) -> Counter[str]:
    return Counter(_DIAGNOSTIC_TOKEN_RE.findall(text.casefold()))


def _token_multiset_similarity(
    requested_tokens: Counter[str],
    requested_count: int,
    candidate: str,
) -> float:
    candidate_tokens = _token_multiset(candidate)
    candidate_count = sum(candidate_tokens.values())
    denominator = requested_count + candidate_count
    if denominator == 0:
        return 1.0
    overlap = sum((requested_tokens & candidate_tokens).values())
    return 2.0 * overlap / denominator


class PromptCache:
    def __init__(self, path: str | Path, read_only: bool):
        self.path = Path(path)
        self.read_only = read_only
        self.unexpected_prompt = False
        self.unexpected_prompt_diagnostics: list[dict[str, Any]] = []
        self._records: dict[str, PromptRecord] = {}
        if self.path.exists():
            self._load()
        elif self.read_only:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> None:
        with self.path.open("r", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            try:
                text = handle.read()
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        # JSONL records are delimited only by ASCII LF. ``str.splitlines()``
        # also splits valid U+0085/U+2028/U+2029 characters inside JSON strings.
        for line_no, line in enumerate(text.split("\n"), start=1):
            if not line.strip():
                continue
            obj = json.loads(line)
            record = PromptRecord(**obj)
            existing = self._records.get(record.prompt_hash)
            if existing and existing.raw_response != record.raw_response:
                raise ValueError(f"cache contains conflicting responses for {record.prompt_hash} at line {line_no}")
            self._records[record.prompt_hash] = record

    def get(self, parts: PromptParts) -> PromptRecord | None:
        return self._records.get(compute_prompt_hash(parts))

    def record_unexpected(self, parts: PromptParts) -> dict[str, Any]:
        prompt_hash = compute_prompt_hash(parts)
        for diagnostic in self.unexpected_prompt_diagnostics:
            if diagnostic["prompt_hash"] == prompt_hash:
                self.unexpected_prompt = True
                return diagnostic

        component_hashes = _component_hashes(parts)
        prompt_name = _prompt_name(parts)
        nearest = self._nearest_record(parts, component_hashes, prompt_name)
        diagnostic = {
            "prompt_hash": prompt_hash,
            "prompt_name": prompt_name,
            "component_hashes": component_hashes,
            "requested_prompt_parts": asdict(parts),
            "requested_lengths": {
                "system_prompt": len(parts.system_prompt),
                "user_prompt": len(parts.user_prompt),
            },
            "nearest_cache_record": nearest,
        }
        self.unexpected_prompt = True
        self.unexpected_prompt_diagnostics.append(diagnostic)
        return diagnostic

    def _nearest_record(
        self,
        requested: PromptParts,
        requested_hashes: dict[str, str],
        requested_name: str | None,
    ) -> dict[str, Any] | None:
        candidates = []
        for record in self._records.values():
            if not record.prompt_parts:
                continue
            candidate_hashes = _component_hashes(record.prompt_parts)
            non_user_matches = sum(
                requested_hashes[name] == candidate_hashes[name]
                for name in (
                    "model_revision",
                    "decoding_config",
                    "structured_output_schema",
                    "system_prompt",
                )
            )
            candidate_user = str(record.prompt_parts.get("user_prompt", ""))
            candidates.append(
                (
                    record,
                    candidate_hashes,
                    non_user_matches,
                    _prompt_name(record.prompt_parts) == requested_name,
                    candidate_user,
                )
            )
        if not candidates:
            return None

        shortlist = sorted(
            candidates,
            key=lambda item: (
                -int(item[3]),
                -item[2],
                abs(len(item[4]) - len(requested.user_prompt)),
                item[0].prompt_hash,
            ),
        )[:16]
        requested_tokens = _token_multiset(requested.user_prompt)
        requested_token_count = sum(requested_tokens.values())
        scored = [
            (
                *item,
                _token_multiset_similarity(
                    requested_tokens,
                    requested_token_count,
                    item[4],
                ),
            )
            for item in shortlist
        ]
        best = max(
            scored,
            key=lambda item: (
                int(item[3]),
                item[2],
                item[5],
                -abs(len(item[4]) - len(requested.user_prompt)),
                item[0].prompt_hash,
            ),
        )
        record, candidate_hashes, _, name_matches, candidate_user, similarity = best
        component_matches = {
            name: requested_hashes[name] == candidate_hashes[name]
            for name in requested_hashes
        }
        return {
            "prompt_hash": record.prompt_hash,
            "prompt_name": _prompt_name(record.prompt_parts),
            "prompt_name_matches": name_matches,
            "component_hashes": candidate_hashes,
            "component_matches": component_matches,
            "system_prompt_length": len(str(record.prompt_parts.get("system_prompt", ""))),
            "user_prompt_length": len(candidate_user),
            "user_prompt_similarity": similarity,
            "user_prompt_similarity_method": "token_multiset_dice",
        }

    def put(
        self,
        parts: PromptParts,
        raw_response: str,
        parsed_response: Any,
        token_usage: dict[str, Any] | None = None,
    ) -> PromptRecord:
        if self.read_only:
            raise RuntimeError("cannot write to read-only prompt cache")
        prompt_hash = compute_prompt_hash(parts)
        record = PromptRecord(
            prompt_hash,
            raw_response,
            parsed_response,
            token_usage or {},
            asdict(parts),
        )
        existing = self._records.get(prompt_hash)
        if existing:
            if existing.raw_response != raw_response:
                raise ValueError(f"attempted to overwrite prompt cache entry {prompt_hash}")
            return existing
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.write(
                    json.dumps(asdict(record), ensure_ascii=False, sort_keys=True)
                    + "\n"
                )
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        self._records[prompt_hash] = record
        return record

    def resolve(self, parts: PromptParts, live_call: Callable[[], PromptRecord | Any] | None = None) -> PromptRecord:
        record = self.get(parts)
        if record is not None:
            return record
        prompt_hash = compute_prompt_hash(parts)
        if self.read_only:
            diagnostic = self.record_unexpected(parts)
            raise UnexpectedPromptError(prompt_hash, diagnostic)
        if live_call is None:
            raise ValueError("live_call is required on writable cache miss")
        result = live_call()
        if isinstance(result, PromptRecord):
            return self.put(parts, result.raw_response, result.parsed_response, result.token_usage)
        raw_response = json.dumps(result, ensure_ascii=False, sort_keys=True)
        return self.put(parts, raw_response=raw_response, parsed_response=result, token_usage={})


class GraphitiPromptCacheLLM(LLMClient):
    """Wrap a Graphiti LLMClient for correctness capture/replay."""

    def __init__(
        self,
        inner: Any,
        cache: PromptCache,
        model_revision: str,
        decoding_config: dict[str, Any],
    ) -> None:
        self.inner = inner
        self.cache = cache
        self.model_revision = model_revision
        self.decoding_config = decoding_config
        self.config = getattr(inner, "config", None)
        self.model = getattr(inner, "model", None)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def _generate_response(
        self,
        messages: Any,
        response_model: Any = None,
        max_tokens: int = 2048,
        model_size: Any = None,
    ) -> Any:
        return await self.inner._generate_response(
            messages,
            response_model=response_model,
            max_tokens=max_tokens,
            model_size=model_size,
        )

    async def generate_response(self, messages: Any, response_model: Any = None, **kwargs: Any) -> Any:
        requested_max_tokens = kwargs.get("max_tokens")
        frozen_max_tokens = self.decoding_config.get("max_tokens")
        effective_max_tokens = requested_max_tokens
        if frozen_max_tokens is not None:
            effective_max_tokens = min(
                int(requested_max_tokens) if requested_max_tokens is not None else int(frozen_max_tokens),
                int(frozen_max_tokens),
            )
        call_config = {
            "prompt_name": kwargs.get("prompt_name"),
            "group_id": kwargs.get("group_id"),
            "max_tokens": effective_max_tokens,
            "model_size": _plain_value(kwargs.get("model_size")),
            "attribute_extraction": bool(kwargs.get("attribute_extraction", False)),
        }
        parts = PromptParts(
            model_revision=self.model_revision,
            decoding_config={**self.decoding_config, **call_config},
            structured_output_schema=_schema_for(response_model),
            system_prompt=_messages_by_role(messages, "system"),
            user_prompt=_messages_by_role(messages, "user"),
        )
        cached = self.cache.get(parts)
        if cached is not None:
            return cached.parsed_response
        if self.cache.read_only:
            diagnostic = self.cache.record_unexpected(parts)
            raise UnexpectedPromptError(compute_prompt_hash(parts), diagnostic)
        parsed = await self.inner.generate_response(messages, response_model=response_model, **kwargs)
        call_record = None
        consume = getattr(self.inner, "consume_last_call_record", None)
        if callable(consume):
            call_record = consume()
        raw_response = (
            call_record.get("raw_response")
            if isinstance(call_record, dict) and isinstance(call_record.get("raw_response"), str)
            else json.dumps(parsed, ensure_ascii=False, sort_keys=True, default=str)
        )
        token_usage = (
            call_record.get("token_usage", {})
            if isinstance(call_record, dict) and isinstance(call_record.get("token_usage", {}), dict)
            else {}
        )
        self.cache.put(
            parts,
            raw_response=raw_response,
            parsed_response=parsed,
            token_usage=token_usage,
        )
        return parsed


def _schema_for(response_model: Any) -> dict[str, Any]:
    if response_model is None:
        return {}
    if hasattr(response_model, "model_json_schema"):
        return constrain_single_episode_indices(response_model.model_json_schema())
    return {"name": getattr(response_model, "__name__", str(response_model))}


def _messages_by_role(messages: Any, role: str) -> str:
    parts = []
    for message in messages or []:
        msg_role = getattr(message, "role", None)
        content = getattr(message, "content", None)
        if isinstance(message, dict):
            msg_role = message.get("role")
            content = message.get("content")
        if msg_role == role and content is not None:
            parts.append(str(content))
    return "\n".join(parts)


def _plain_value(value: Any) -> Any:
    return getattr(value, "value", value)
