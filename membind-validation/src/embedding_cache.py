"""Exact single-item embedding capture/replay with fail-closed integrity checks."""

from __future__ import annotations

import asyncio
import base64
import fcntl
import hashlib
import json
import math
import os
import re
import struct
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from graphiti_core.embedder.client import EmbedderClient
from instrumentation import current_episode_key


EMBEDDING_CACHE_SCHEMA = "membind.embedding_oracle.v1"
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_IDENTITY_KINDS = {"endpoint_revision", "deployment_fingerprint"}


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _text_bytes(value: str) -> bytes:
    if not isinstance(value, str):
        raise TypeError("embedding cache inputs must be strings")
    try:
        return value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("embedding input is not valid UTF-8") from exc


def _vector_values(value: Any, expected_dimension: int) -> list[float]:
    if isinstance(value, (str, bytes)):
        raise ValueError("embedding vector must be a numeric sequence")
    try:
        vector = [float(item) for item in value]
    except (TypeError, ValueError) as exc:
        raise ValueError("embedding vector must contain numeric values") from exc
    if len(vector) != expected_dimension:
        raise ValueError(
            f"embedding vector dimension {len(vector)} does not match "
            f"namespace dimension {expected_dimension}"
        )
    if any(not math.isfinite(item) for item in vector):
        raise ValueError("embedding vector values must all be finite")
    return vector


def _vector_sha256(vector: list[float]) -> str:
    digest = hashlib.sha256()
    for value in vector:
        digest.update(struct.pack(">d", value))
    return digest.hexdigest()


@dataclass(frozen=True)
class EmbeddingNamespace:
    served_model_id: str
    identity_kind: str
    identity_value: str
    dimension: int
    dtype: str
    pooling: str
    normalization: str
    instruction_policy: str
    input_transform: str
    tokenizer_fingerprint: str | None = None
    model_fingerprint: str | None = None
    schema_version: str = EMBEDDING_CACHE_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != EMBEDDING_CACHE_SCHEMA:
            raise ValueError(f"unsupported embedding namespace schema: {self.schema_version}")
        if not self.served_model_id.strip():
            raise ValueError("served embedding model ID is required")
        if self.identity_kind not in _IDENTITY_KINDS:
            raise ValueError(
                "embedding identity must use endpoint_revision or deployment_fingerprint"
            )
        identity = self.identity_value.strip()
        if self.identity_kind == "deployment_fingerprint" and not _SHA256_RE.fullmatch(
            identity
        ):
            raise ValueError(
                "immutable deployment fingerprint must be a lowercase SHA256 value"
            )
        if self.identity_kind == "endpoint_revision" and (
            not identity
            or identity.casefold() in {"unknown", "unreported", "endpoint-unreported"}
            or identity == self.served_model_id
            or identity.startswith(("http://", "https://"))
        ):
            raise ValueError("endpoint revision must be an immutable reported revision")
        if int(self.dimension) <= 0:
            raise ValueError("embedding namespace dimension must be positive")
        for name in (
            "dtype",
            "pooling",
            "normalization",
            "instruction_policy",
            "input_transform",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"embedding namespace {name} is required")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "EmbeddingNamespace":
        if not isinstance(value, dict):
            raise TypeError("embedding namespace must be a dictionary")
        try:
            return cls(**value)
        except TypeError as exc:
            raise ValueError("invalid embedding namespace fields") from exc

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def sha256(self) -> str:
        return _sha256_bytes(_canonical_json(self.to_dict()).encode("ascii"))


class UnexpectedEmbeddingError(RuntimeError):
    def __init__(self, diagnostic: dict[str, Any]):
        self.text_sha256 = str(diagnostic["text_sha256"])
        self.text_length = int(diagnostic["text_length"])
        self.text_byte_length = int(diagnostic["text_byte_length"])
        self.namespace_sha256 = str(diagnostic["namespace_sha256"])
        self.diagnostic = dict(diagnostic)
        super().__init__(
            "unexpected embedding input during read-only replay: "
            + self.text_sha256
        )


class EmbeddingCache:
    def __init__(
        self,
        path: str | Path,
        read_only: bool,
        namespace: EmbeddingNamespace,
    ) -> None:
        self.path = Path(path)
        self.read_only = bool(read_only)
        self.namespace = namespace
        self.unexpected_embedding = False
        self.unexpected_embedding_diagnostics: list[dict[str, Any]] = []
        self.successful_hit_count = 0
        self._records: dict[str, tuple[bytes, list[float]]] = {}
        if self.read_only:
            if not self.path.exists():
                raise FileNotFoundError(
                    f"missing embedding capture cache: {self.path}"
                )
            self._load()
        else:
            self._create()

    def _create(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        header = {
            "record_type": "namespace",
            "namespace": self.namespace.to_dict(),
            "namespace_sha256": self.namespace.sha256,
        }
        with self.path.open("x", encoding="ascii", newline="\n") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.write(_canonical_json(header) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _load(self) -> None:
        with self.path.open("r", encoding="ascii", newline="") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            try:
                raw = handle.read()
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        parsed: list[tuple[int, dict[str, Any]]] = []
        for line_number, line in enumerate(raw.split("\n"), start=1):
            if not line:
                continue
            try:
                value = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ValueError(
                    f"invalid embedding cache JSON at line {line_number}"
                ) from exc
            if not isinstance(value, dict):
                raise ValueError(
                    f"embedding cache record at line {line_number} is not an object"
                )
            parsed.append((line_number, value))
        if not parsed or parsed[0][1].get("record_type") != "namespace":
            raise ValueError("embedding cache is missing its namespace header")
        if any(value.get("record_type") == "namespace" for _, value in parsed[1:]):
            raise ValueError("embedding cache contains a duplicate namespace header")
        header = parsed[0][1]
        try:
            stored_namespace = EmbeddingNamespace.from_dict(header["namespace"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("embedding cache has an invalid namespace header") from exc
        if (
            header.get("namespace_sha256") != stored_namespace.sha256
            or stored_namespace != self.namespace
        ):
            raise ValueError("embedding cache namespace mismatch")
        for line_number, value in parsed[1:]:
            self._load_embedding_record(value, line_number)

    def _load_embedding_record(self, record: dict[str, Any], line_number: int) -> None:
        if record.get("record_type") != "embedding":
            raise ValueError(f"unknown embedding cache record at line {line_number}")
        required = {
            "namespace_sha256",
            "item_sha256",
            "input_utf8_b64",
            "text_length",
            "text_byte_length",
            "dimension",
            "vector_sha256",
            "vector",
        }
        if not required.issubset(record):
            raise ValueError(f"embedding cache record is incomplete at line {line_number}")
        if record["namespace_sha256"] != self.namespace.sha256:
            raise ValueError(f"embedding record namespace mismatch at line {line_number}")
        try:
            input_bytes = base64.b64decode(record["input_utf8_b64"], validate=True)
            input_text = input_bytes.decode("utf-8")
        except (ValueError, UnicodeDecodeError, TypeError) as exc:
            raise ValueError(f"invalid embedding input bytes at line {line_number}") from exc
        item_sha256 = _sha256_bytes(input_bytes)
        if record["item_sha256"] != item_sha256:
            raise ValueError(f"embedding input hash mismatch at line {line_number}")
        if int(record["text_length"]) != len(input_text) or int(
            record["text_byte_length"]
        ) != len(input_bytes):
            raise ValueError(f"embedding input length mismatch at line {line_number}")
        vector = _vector_values(record["vector"], self.namespace.dimension)
        if int(record["dimension"]) != self.namespace.dimension:
            raise ValueError(f"embedding vector dimension mismatch at line {line_number}")
        if record["vector_sha256"] != _vector_sha256(vector):
            raise ValueError(f"embedding vector hash mismatch at line {line_number}")
        existing = self._records.get(item_sha256)
        if existing is not None:
            if existing[0] != input_bytes or existing[1] != vector:
                raise ValueError(
                    f"conflicting embedding for item {item_sha256} at line {line_number}"
                )
            return
        self._records[item_sha256] = (input_bytes, vector)

    def get(self, text: str) -> list[float] | None:
        input_bytes = _text_bytes(text)
        record = self._records.get(_sha256_bytes(input_bytes))
        if record is None:
            return None
        if record[0] != input_bytes:
            raise ValueError("embedding input SHA256 collision detected")
        self.successful_hit_count += 1
        return list(record[1])

    def record_unexpected(
        self,
        text: str,
        *,
        call_shape: str = "create",
        input_ordinal: int = 0,
    ) -> dict[str, Any]:
        input_bytes = _text_bytes(text)
        text_sha256 = _sha256_bytes(input_bytes)
        for diagnostic in self.unexpected_embedding_diagnostics:
            if (
                diagnostic["text_sha256"] == text_sha256
                and diagnostic["call_shape"] == call_shape
                and diagnostic["input_ordinal"] == input_ordinal
            ):
                self.unexpected_embedding = True
                return diagnostic
        episode_key = current_episode_key()
        diagnostic = {
            "text_sha256": text_sha256,
            "text_length": len(text),
            "text_byte_length": len(input_bytes),
            "namespace_sha256": self.namespace.sha256,
            "episode_key": list(episode_key) if episode_key is not None else None,
            "source_sequence": (
                int(episode_key[1])
                if isinstance(episode_key, tuple) and len(episode_key) == 2
                else None
            ),
            "call_shape": call_shape,
            "input_ordinal": int(input_ordinal),
            "previous_successful_hit_count": self.successful_hit_count,
        }
        self.unexpected_embedding = True
        self.unexpected_embedding_diagnostics.append(diagnostic)
        return diagnostic

    def put(self, text: str, value: Any) -> list[float]:
        if self.read_only:
            raise RuntimeError("cannot write to read-only embedding cache")
        input_bytes = _text_bytes(text)
        item_sha256 = _sha256_bytes(input_bytes)
        vector = _vector_values(value, self.namespace.dimension)
        existing = self._records.get(item_sha256)
        if existing is not None:
            if existing[0] != input_bytes or existing[1] != vector:
                raise ValueError(f"conflicting embedding for item {item_sha256}")
            return list(existing[1])
        record = {
            "record_type": "embedding",
            "namespace_sha256": self.namespace.sha256,
            "item_sha256": item_sha256,
            "input_utf8_b64": base64.b64encode(input_bytes).decode("ascii"),
            "text_length": len(text),
            "text_byte_length": len(input_bytes),
            "dimension": len(vector),
            "vector_sha256": _vector_sha256(vector),
            "vector": vector,
        }
        with self.path.open("a", encoding="ascii", newline="\n") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.write(_canonical_json(record) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        self._records[item_sha256] = (input_bytes, vector)
        return list(vector)


def _single_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], str):
        return value[0]
    return None


def _cache_key(value: Any) -> str:
    text = _single_text(value)
    if text is not None:
        return "text:" + _sha256_bytes(_text_bytes(text))
    return "value:" + _sha256_bytes(_canonical_json(value).encode("ascii"))


class CachingCountingEmbedder(EmbedderClient):
    def __init__(
        self,
        inner: Any,
        persistent_cache: EmbeddingCache | None = None,
    ) -> None:
        self.inner = inner
        self.persistent_cache = persistent_cache
        self.config = getattr(inner, "config", None)
        self.api_call_count = 0
        self.text_count = 0
        self.cache_hit_count = 0
        self.call_events: list[dict[str, Any]] = []
        self._cache: dict[str, list[float]] = {}
        self._inflight: dict[str, asyncio.Future[list[float]]] = {}
        self._lock = asyncio.Lock()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def _required_text(self, value: Any) -> str | None:
        text = _single_text(value)
        if self.persistent_cache is not None and text is None:
            raise ValueError(
                "persistent embedding oracle create() requires one exact string item"
            )
        return text

    async def create(self, input_data: Any) -> list[float]:
        text = self._required_text(input_data)
        key = _cache_key(input_data)
        owner = False
        async with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self.cache_hit_count += 1
                return list(cached)
            if self.persistent_cache is not None and text is not None:
                persisted = self.persistent_cache.get(text)
                if persisted is not None:
                    self._cache[key] = persisted
                    self.cache_hit_count += 1
                    return list(persisted)
                if self.persistent_cache.read_only:
                    diagnostic = self.persistent_cache.record_unexpected(
                        text,
                        call_shape="create",
                    )
                    raise UnexpectedEmbeddingError(diagnostic)
            future = self._inflight.get(key)
            if future is None:
                future = asyncio.get_running_loop().create_future()
                self._inflight[key] = future
                owner = True
            else:
                self.cache_hit_count += 1

        if owner:
            self.api_call_count += 1
            self.text_count += 1
            self.call_events.append(
                {"episode_key": current_episode_key(), "text_count": 1}
            )
            try:
                vector = list(await self.inner.create(input_data))
                if self.persistent_cache is not None and text is not None:
                    vector = self.persistent_cache.put(text, vector)
            except BaseException as exc:
                await self._fail([key], [future], exc)
                raise
            await self._succeed(key, future, vector)

        return list(await future)

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        if not input_data_list:
            return []
        if any(not isinstance(value, str) for value in input_data_list):
            raise TypeError("embedding batch inputs must all be strings")

        keys = [_cache_key(value) for value in input_data_list]
        futures: list[asyncio.Future[list[float]]] = []
        owner_keys: list[str] = []
        owner_values: list[str] = []
        async with self._lock:
            loop = asyncio.get_running_loop()
            for ordinal, (key, value) in enumerate(
                zip(keys, input_data_list, strict=True)
            ):
                cached = self._cache.get(key)
                if cached is not None:
                    future = loop.create_future()
                    future.set_result(cached)
                    self.cache_hit_count += 1
                else:
                    persisted = (
                        self.persistent_cache.get(value)
                        if self.persistent_cache is not None
                        else None
                    )
                    if persisted is not None:
                        self._cache[key] = persisted
                        future = loop.create_future()
                        future.set_result(persisted)
                        self.cache_hit_count += 1
                    elif (
                        self.persistent_cache is not None
                        and self.persistent_cache.read_only
                    ):
                        diagnostic = self.persistent_cache.record_unexpected(
                            value,
                            call_shape="create_batch",
                            input_ordinal=ordinal,
                        )
                        raise UnexpectedEmbeddingError(diagnostic)
                    else:
                        future = self._inflight.get(key)
                        if future is None:
                            future = loop.create_future()
                            self._inflight[key] = future
                            owner_keys.append(key)
                            owner_values.append(value)
                        else:
                            self.cache_hit_count += 1
                futures.append(future)

        if owner_values:
            self.api_call_count += 1
            self.text_count += len(owner_values)
            self.call_events.append(
                {
                    "episode_key": current_episode_key(),
                    "text_count": len(owner_values),
                }
            )
            owner_futures = [self._inflight[key] for key in owner_keys]
            try:
                vectors = await self.inner.create_batch(owner_values)
                if len(vectors) != len(owner_values):
                    raise RuntimeError(
                        "embedding batch returned the wrong number of vectors"
                    )
                if self.persistent_cache is not None:
                    validated = [
                        _vector_values(vector, self.persistent_cache.namespace.dimension)
                        for vector in vectors
                    ]
                    vectors = [
                        self.persistent_cache.put(value, vector)
                        for value, vector in zip(owner_values, validated, strict=True)
                    ]
            except BaseException as exc:
                await self._fail(owner_keys, owner_futures, exc)
                raise
            for key, future, vector in zip(
                owner_keys, owner_futures, vectors, strict=True
            ):
                await self._succeed(key, future, list(vector))

        return [list(vector) for vector in await asyncio.gather(*futures)]

    async def _succeed(
        self,
        key: str,
        future: asyncio.Future[list[float]],
        vector: list[float],
    ) -> None:
        async with self._lock:
            self._cache[key] = list(vector)
            self._inflight.pop(key, None)
            if not future.done():
                future.set_result(list(vector))

    async def _fail(
        self,
        keys: list[str],
        futures: list[asyncio.Future[list[float]]],
        exc: BaseException,
    ) -> None:
        async with self._lock:
            for key, future in zip(keys, futures, strict=True):
                self._inflight.pop(key, None)
                if not future.done():
                    future.set_exception(exc)
                    future.exception()


def embedding_metrics(embedder: Any) -> dict[str, int]:
    cache = getattr(embedder, "persistent_cache", None)
    return {
        "embedding_call_count": int(getattr(embedder, "api_call_count", 0)),
        "embedding_text_count": int(getattr(embedder, "text_count", 0)),
        "embedding_cache_hits": int(getattr(embedder, "cache_hit_count", 0)),
        "embedding_oracle_successful_hits": int(
            getattr(cache, "successful_hit_count", 0)
        ),
        "embedding_oracle_misses": len(
            getattr(cache, "unexpected_embedding_diagnostics", []) or []
        ),
    }


def unexpected_embedding_records(embedder: Any) -> list[dict[str, Any]]:
    cache = getattr(embedder, "persistent_cache", None)
    return list(getattr(cache, "unexpected_embedding_diagnostics", []) or [])
