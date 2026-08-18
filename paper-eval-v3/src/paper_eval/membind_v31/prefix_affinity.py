"""Exact-token, content-safe prefix metadata for v3.1 cache-affine admission.

The scheduler never retains prompt text. It tokenizes the same effective Qwen
chat request that the pinned Graphiti client sends, then exposes only a full
sequence digest and a rolling digest at each backend match boundary. Those
rolling identities are sufficient to compute exact granularity-aligned LCP
values without publishing reconstructable token IDs.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import struct
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from paper_eval.artifacts import payload_sha256


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
DEFAULT_PREFIX_MATCH_UNIT = 16
TOKENIZER_REPO_ID = "Qwen/Qwen3-32B-FP8"
TOKENIZER_REVISION = "aa55da1ecc13d006e8b8e4f54579b1ea8c3db2df"
DEFAULT_TOKENIZER_ROOT = Path(
    "/data/predator/ly/Mem/cache/huggingface/"
    "models--Qwen--Qwen3-32B-FP8/snapshots/"
    f"{TOKENIZER_REVISION}"
)
TOKENIZER_FILE_SHA256S = {
    "config.json": "e546dacd2c772660270233f5579e9ab923cc2a7ec5ed3c58c27c2bc62cbf5169",
    "merges.txt": "8831e4f1a044471340f7c0a83d7bd71306a5b867e95fd870f74d0c5308a904d5",
    "tokenizer.json": "aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4",
    "tokenizer_config.json": "d5d09f07b48c3086c508b30d1c9114bd1189145b74e982a265350c923acd8101",
    "vocab.json": "ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910",
}
VLLM_CACHE_CONFIG_SOURCE = (
    "https://raw.githubusercontent.com/vllm-project/vllm/"
    "v0.26.0/vllm/config/cache.py"
)
VLLM_CACHE_CONFIG_SHA256 = (
    "ee2c0db3e4e6c9e9cab33d8be566c4b8101159d36c0d3787c30d47931ee2a9a4"
)


class MemBindV31PrefixAffinityError(ValueError):
    """Tokenization, identity, or prefix-index evidence failed closed."""


def _fail(code: str) -> MemBindV31PrefixAffinityError:
    return MemBindV31PrefixAffinityError(code)


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def _positive(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _fail(code)
    return value


def _nonnegative(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _token_bytes(token_ids: Sequence[int]) -> bytes:
    chunks: list[bytes] = []
    for value in token_ids:
        token = _nonnegative(value, "token_id_invalid")
        if token > 0xFFFFFFFF:
            raise _fail("token_id_invalid")
        chunks.append(struct.pack(">I", token))
    if not chunks:
        raise _fail("token_ids_empty")
    return b"".join(chunks)


def _metadata_body(
    *,
    token_count: int,
    prefix_match_unit: int,
    token_sequence_hmac_sha256: str,
    block_prefix_hmac_sha256s: Sequence[str],
    tokenizer_identity_sha256: str,
    trace_key_identity_sha256: str,
    cache_identity_sha256: str,
) -> dict[str, object]:
    return {
        "schema_version": "membind.paper-eval-v3.prefix-metadata.v2",
        "token_count": token_count,
        "prefix_match_unit": prefix_match_unit,
        "token_sequence_hmac_sha256": token_sequence_hmac_sha256,
        "block_prefix_hmac_sha256s": list(block_prefix_hmac_sha256s),
        "tokenizer_identity_sha256": tokenizer_identity_sha256,
        "trace_key_identity_sha256": trace_key_identity_sha256,
        "cache_identity_sha256": cache_identity_sha256,
    }


@dataclass(frozen=True, slots=True)
class PrefixMetadata:
    """Content-safe exact-token identity at each backend match boundary."""

    token_count: int
    prefix_match_unit: int
    token_sequence_hmac_sha256: str
    block_prefix_hmac_sha256s: tuple[str, ...]
    tokenizer_identity_sha256: str
    trace_key_identity_sha256: str
    cache_identity_sha256: str
    metadata_sha256: str

    @classmethod
    def from_token_ids(
        cls,
        token_ids: Sequence[int],
        *,
        prefix_match_unit: int,
        tokenizer_identity_sha256: str,
        cache_identity_sha256: str,
        trace_hmac_key: bytes,
    ) -> "PrefixMetadata":
        if isinstance(token_ids, (str, bytes)) or not isinstance(token_ids, Sequence):
            raise _fail("token_ids_invalid")
        unit = _positive(prefix_match_unit, "prefix_match_unit_invalid")
        identity = _sha(tokenizer_identity_sha256, "tokenizer_identity_invalid")
        cache_identity = _sha(cache_identity_sha256, "cache_identity_invalid")
        if not isinstance(trace_hmac_key, bytes) or len(trace_hmac_key) < 32:
            raise _fail("trace_hmac_key_invalid")
        key_identity = hashlib.sha256(trace_hmac_key).hexdigest()
        packed = _token_bytes(token_ids)
        token_count = len(token_ids)
        rolling = b""
        prefixes: list[str] = []
        for start in range(0, token_count - unit + 1, unit):
            block = packed[start * 4 : (start + unit) * 4]
            rolling = hmac.new(
                trace_hmac_key, b"membind-prefix-v1\0" + rolling + block, hashlib.sha256
            ).digest()
            prefixes.append(rolling.hex())
        body = _metadata_body(
            token_count=token_count,
            prefix_match_unit=unit,
            token_sequence_hmac_sha256=hmac.new(
                trace_hmac_key, b"membind-token-sequence-v1\0" + packed, hashlib.sha256
            ).hexdigest(),
            block_prefix_hmac_sha256s=prefixes,
            tokenizer_identity_sha256=identity,
            trace_key_identity_sha256=key_identity,
            cache_identity_sha256=cache_identity,
        )
        return cls(
            token_count=token_count,
            prefix_match_unit=unit,
            token_sequence_hmac_sha256=str(body["token_sequence_hmac_sha256"]),
            block_prefix_hmac_sha256s=tuple(prefixes),
            tokenizer_identity_sha256=identity,
            trace_key_identity_sha256=key_identity,
            cache_identity_sha256=cache_identity,
            metadata_sha256=payload_sha256(body),
        )

    def verify(self) -> "PrefixMetadata":
        count = _positive(self.token_count, "token_count_invalid")
        unit = _positive(self.prefix_match_unit, "prefix_match_unit_invalid")
        sequence_hash = _sha(
            self.token_sequence_hmac_sha256, "token_sequence_hmac_invalid"
        )
        identity = _sha(self.tokenizer_identity_sha256, "tokenizer_identity_invalid")
        key_identity = _sha(
            self.trace_key_identity_sha256, "trace_key_identity_invalid"
        )
        cache_identity = _sha(self.cache_identity_sha256, "cache_identity_invalid")
        if (
            not isinstance(self.block_prefix_hmac_sha256s, tuple)
            or len(self.block_prefix_hmac_sha256s) != count // unit
            or any(
                not isinstance(value, str) or _SHA256.fullmatch(value) is None
                for value in self.block_prefix_hmac_sha256s
            )
        ):
            raise _fail("block_prefix_identity_invalid")
        expected = payload_sha256(
            _metadata_body(
                token_count=count,
                prefix_match_unit=unit,
                token_sequence_hmac_sha256=sequence_hash,
                block_prefix_hmac_sha256s=self.block_prefix_hmac_sha256s,
                tokenizer_identity_sha256=identity,
                trace_key_identity_sha256=key_identity,
                cache_identity_sha256=cache_identity,
            )
        )
        if _sha(self.metadata_sha256, "prefix_metadata_hash_invalid") != expected:
            raise _fail("prefix_metadata_hash_mismatch")
        return self

    def aligned_lcp(self, other: "PrefixMetadata") -> int:
        if not isinstance(other, PrefixMetadata):
            raise _fail("prefix_metadata_invalid")
        left = self.verify()
        right = other.verify()
        if (
            left.prefix_match_unit != right.prefix_match_unit
            or left.tokenizer_identity_sha256 != right.tokenizer_identity_sha256
            or left.trace_key_identity_sha256 != right.trace_key_identity_sha256
            or left.cache_identity_sha256 != right.cache_identity_sha256
        ):
            raise _fail("prefix_identity_mismatch")
        matched = 0
        for first, second in zip(
            left.block_prefix_hmac_sha256s, right.block_prefix_hmac_sha256s
        ):
            if first != second:
                break
            matched += 1
        return matched * left.prefix_match_unit

    def public_projection(self) -> dict[str, object]:
        self.verify()
        return {
            "token_count": self.token_count,
            "prefix_match_unit": self.prefix_match_unit,
            "token_sequence_hmac_sha256": self.token_sequence_hmac_sha256,
            "token_prefix_block_hmac_sha256s": list(
                self.block_prefix_hmac_sha256s
            ),
            "tokenizer_identity_sha256": self.tokenizer_identity_sha256,
            "trace_key_identity_sha256": self.trace_key_identity_sha256,
            "cache_identity_sha256": self.cache_identity_sha256,
            "prefix_metadata_sha256": self.metadata_sha256,
        }


class PrefixProviderIndex:
    """Most-recent completed provider for each exact rolling block prefix."""

    def __init__(self, *, prefix_match_unit: int) -> None:
        self._unit = _positive(prefix_match_unit, "prefix_match_unit_invalid")
        self._providers: dict[str, int] = {}
        self._last_completion = -1
        self._completion_count = 0
        self._identity: tuple[str, str, str] | None = None

    @property
    def prefix_match_unit(self) -> int:
        return self._unit

    @property
    def completion_count(self) -> int:
        return self._completion_count

    def _metadata(self, value: PrefixMetadata) -> PrefixMetadata:
        if not isinstance(value, PrefixMetadata):
            raise _fail("prefix_metadata_invalid")
        selected = value.verify()
        if selected.prefix_match_unit != self._unit:
            raise _fail("prefix_match_unit_mismatch")
        identity = (
            selected.tokenizer_identity_sha256,
            selected.trace_key_identity_sha256,
            selected.cache_identity_sha256,
        )
        if self._identity is None:
            self._identity = identity
        elif self._identity != identity:
            raise _fail("prefix_identity_mismatch")
        return selected

    def register_completed(
        self, metadata: PrefixMetadata, *, completion_sequence: int
    ) -> None:
        selected = self._metadata(metadata)
        sequence = _nonnegative(completion_sequence, "completion_sequence_invalid")
        if sequence <= self._last_completion:
            raise _fail("completion_sequence_not_monotonic")
        for prefix in selected.block_prefix_hmac_sha256s:
            self._providers[prefix] = sequence
        self._last_completion = sequence
        self._completion_count += 1

    def affinity(self, metadata: PrefixMetadata) -> tuple[int, int]:
        selected = self._metadata(metadata)
        matched = 0
        recency = 0
        for prefix in selected.block_prefix_hmac_sha256s:
            provider = self._providers.get(prefix)
            if provider is None:
                break
            matched += 1
            recency = provider
        return matched * self._unit, recency


class QwenGraphitiPrefixEncoder:
    """Project Graphiti's pre-transport request into exact Qwen token IDs."""

    def __init__(
        self,
        *,
        inner: object,
        tokenizer: object,
        prefix_match_unit: int,
        tokenizer_identity_sha256: str,
        cache_identity_sha256: str,
        trace_hmac_key: bytes,
        multilingual_instruction: Callable[[str | None], str],
    ) -> None:
        if inner is None or not callable(getattr(tokenizer, "apply_chat_template", None)):
            raise _fail("prefix_encoder_dependency_invalid")
        if not callable(multilingual_instruction):
            raise _fail("multilingual_instruction_invalid")
        self._inner = inner
        self._tokenizer = tokenizer
        self._unit = _positive(prefix_match_unit, "prefix_match_unit_invalid")
        self._identity = _sha(tokenizer_identity_sha256, "tokenizer_identity_invalid")
        self._cache_identity = _sha(cache_identity_sha256, "cache_identity_invalid")
        if not isinstance(trace_hmac_key, bytes) or len(trace_hmac_key) < 32:
            raise _fail("trace_hmac_key_invalid")
        self._trace_hmac_key = trace_hmac_key
        self._trace_key_identity = hashlib.sha256(trace_hmac_key).hexdigest()
        self._language = multilingual_instruction

    @property
    def public_identity(self) -> dict[str, object]:
        return {
            "schema_version": "membind.paper-eval-v3.qwen-prefix-encoder.v1",
            "tokenizer_identity_sha256": self._identity,
            "trace_key_identity_sha256": self._trace_key_identity,
            "cache_identity_sha256": self._cache_identity,
            "prefix_match_unit": self._unit,
            "chat_template_kwargs": {"enable_thinking": False},
            "add_generation_prompt": True,
        }

    @staticmethod
    def _argument(
        args: Sequence[object], kwargs: Mapping[str, object], index: int, name: str
    ) -> object:
        return args[index] if len(args) > index else kwargs.get(name)

    @staticmethod
    def _content(message: object) -> tuple[str, str]:
        if isinstance(message, Mapping):
            role = message.get("role")
            content = message.get("content")
        else:
            role = getattr(message, "role", None)
            content = getattr(message, "content", None)
        if role not in {"system", "user"} or not isinstance(content, str):
            raise _fail("message_shape_invalid")
        return str(role), content

    def __call__(self, *args: object, **kwargs: object) -> PrefixMetadata:
        raw_messages = self._argument(args, kwargs, 0, "messages")
        if (
            isinstance(raw_messages, (str, bytes))
            or not isinstance(raw_messages, Sequence)
            or not raw_messages
        ):
            raise _fail("messages_invalid")
        messages = deepcopy(list(raw_messages))
        response_model = self._argument(args, kwargs, 1, "response_model")
        group_id = self._argument(args, kwargs, 4, "group_id")
        if group_id is not None and not isinstance(group_id, str):
            raise _fail("group_id_invalid")
        attribute_extraction = kwargs.get("attribute_extraction", False)
        if not isinstance(attribute_extraction, bool):
            raise _fail("attribute_extraction_invalid")
        preamble = getattr(self._inner, "_apply_attribute_extraction_preamble", None)
        if not callable(preamble):
            raise _fail("attribute_preamble_missing")
        preamble(messages, attribute_extraction)

        mode = getattr(self._inner, "structured_output_mode", None)
        if mode not in {"json_schema", "json_object"}:
            raise _fail("structured_output_mode_invalid")
        if response_model is not None and mode == "json_object":
            schema = getattr(response_model, "model_json_schema", None)
            if not callable(schema):
                raise _fail("response_model_invalid")
            role, content = self._content(messages[-1])
            content += (
                "\n\nRespond with a JSON object in the following format:\n\n"
                + json.dumps(schema())
            )
            if isinstance(messages[-1], Mapping):
                messages[-1] = {**dict(messages[-1]), "role": role, "content": content}
            else:
                setattr(messages[-1], "content", content)

        instruction = self._language(group_id)
        if not isinstance(instruction, str):
            raise _fail("multilingual_instruction_invalid")
        first_role, first_content = self._content(messages[0])
        if isinstance(messages[0], Mapping):
            messages[0] = {
                **dict(messages[0]),
                "role": first_role,
                "content": first_content + instruction,
            }
        else:
            setattr(messages[0], "content", first_content + instruction)

        cleaner = getattr(self._inner, "_clean_input", None)
        if not callable(cleaner):
            raise _fail("input_cleaner_missing")
        projected: list[dict[str, str]] = []
        for message in messages:
            role, content = self._content(message)
            cleaned = cleaner(content)
            if not isinstance(cleaned, str):
                raise _fail("cleaned_input_invalid")
            projected.append({"role": role, "content": cleaned})
        try:
            token_ids = self._tokenizer.apply_chat_template(
                projected,
                tokenize=True,
                add_generation_prompt=True,
                enable_thinking=False,
            )
        except Exception:
            raise _fail("chat_template_tokenization_failed") from None
        return PrefixMetadata.from_token_ids(
            token_ids,
            prefix_match_unit=self._unit,
            tokenizer_identity_sha256=self._identity,
            cache_identity_sha256=self._cache_identity,
            trace_hmac_key=self._trace_hmac_key,
        )


class QwenOpenAITransportPrefixEncoder:
    """Tokenize the final OpenAI chat messages seen by an actual HTTP attempt."""

    def __init__(
        self,
        *,
        tokenizer: object,
        prefix_match_unit: int,
        tokenizer_identity_sha256: str,
        cache_identity_sha256: str,
        trace_hmac_key: bytes,
    ) -> None:
        if not callable(getattr(tokenizer, "apply_chat_template", None)):
            raise _fail("prefix_encoder_dependency_invalid")
        self._tokenizer = tokenizer
        self._unit = _positive(prefix_match_unit, "prefix_match_unit_invalid")
        self._identity = _sha(tokenizer_identity_sha256, "tokenizer_identity_invalid")
        self._cache_identity = _sha(cache_identity_sha256, "cache_identity_invalid")
        if not isinstance(trace_hmac_key, bytes) or len(trace_hmac_key) < 32:
            raise _fail("trace_hmac_key_invalid")
        self._trace_hmac_key = trace_hmac_key
        self._trace_key_identity = hashlib.sha256(trace_hmac_key).hexdigest()

    @property
    def public_identity(self) -> dict[str, object]:
        return {
            "schema_version": "membind.paper-eval-v3.qwen-transport-prefix-encoder.v1",
            "tokenizer_identity_sha256": self._identity,
            "trace_key_identity_sha256": self._trace_key_identity,
            "cache_identity_sha256": self._cache_identity,
            "prefix_match_unit": self._unit,
            "boundary": "openai_chat_completions_create",
            "chat_template_kwargs": {"enable_thinking": False},
            "add_generation_prompt": True,
        }

    def __call__(self, *args: object, **kwargs: object) -> PrefixMetadata:
        raw_messages = kwargs.get("messages")
        if raw_messages is None and args:
            raw_messages = args[0]
        if (
            isinstance(raw_messages, (str, bytes))
            or not isinstance(raw_messages, Sequence)
            or not raw_messages
        ):
            raise _fail("messages_invalid")
        projected: list[dict[str, str]] = []
        for message in raw_messages:
            role, content = QwenGraphitiPrefixEncoder._content(message)
            projected.append({"role": role, "content": content})
        extra_body = kwargs.get("extra_body")
        if extra_body is not None and not isinstance(extra_body, Mapping):
            raise _fail("extra_body_invalid")
        chat_kwargs = (
            {} if extra_body is None else dict(extra_body.get("chat_template_kwargs") or {})
        )
        if chat_kwargs.get("enable_thinking", False) is not False:
            raise _fail("enable_thinking_identity_mismatch")
        try:
            token_ids = self._tokenizer.apply_chat_template(
                projected,
                tokenize=True,
                add_generation_prompt=True,
                enable_thinking=False,
            )
        except Exception:
            raise _fail("chat_template_tokenization_failed") from None
        return PrefixMetadata.from_token_ids(
            token_ids,
            prefix_match_unit=self._unit,
            tokenizer_identity_sha256=self._identity,
            cache_identity_sha256=self._cache_identity,
            trace_hmac_key=self._trace_hmac_key,
        )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        raise _fail("tokenizer_file_unreadable") from None
    return digest.hexdigest()


def build_production_qwen_prefix_encoder(
    *,
    inner: object | None = None,
    tokenizer_root: Path = DEFAULT_TOKENIZER_ROOT,
    prefix_match_unit: int = DEFAULT_PREFIX_MATCH_UNIT,
    trace_hmac_key: bytes,
    cache_identity_sha256: str,
) -> QwenOpenAITransportPrefixEncoder:
    """Load only the pinned tokenizer snapshot; never download during a run."""

    root = Path(tokenizer_root)
    observed: dict[str, str] = {}
    for name, expected in TOKENIZER_FILE_SHA256S.items():
        value = _file_sha256(root / name)
        if value != expected:
            raise _fail("tokenizer_file_hash_mismatch")
        observed[name] = value
    identity = payload_sha256(
        {
            "schema_version": "membind.paper-eval-v3.qwen-tokenizer-identity.v1",
            "repo_id": TOKENIZER_REPO_ID,
            "revision": TOKENIZER_REVISION,
            "files": observed,
            "chat_template_kwargs": {"enable_thinking": False},
            "add_generation_prompt": True,
        }
    )
    try:
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(root, local_files_only=True)
    except Exception:
        raise _fail("tokenizer_load_failed") from None
    return QwenOpenAITransportPrefixEncoder(
        tokenizer=tokenizer,
        prefix_match_unit=prefix_match_unit,
        tokenizer_identity_sha256=identity,
        cache_identity_sha256=cache_identity_sha256,
        trace_hmac_key=trace_hmac_key,
    )


__all__ = [
    "DEFAULT_PREFIX_MATCH_UNIT",
    "DEFAULT_TOKENIZER_ROOT",
    "MemBindV31PrefixAffinityError",
    "PrefixMetadata",
    "PrefixProviderIndex",
    "QwenGraphitiPrefixEncoder",
    "QwenOpenAITransportPrefixEncoder",
    "TOKENIZER_FILE_SHA256S",
    "TOKENIZER_REPO_ID",
    "TOKENIZER_REVISION",
    "VLLM_CACHE_CONFIG_SHA256",
    "VLLM_CACHE_CONFIG_SOURCE",
    "build_production_qwen_prefix_encoder",
]
