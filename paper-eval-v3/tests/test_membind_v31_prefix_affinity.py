"""Exact, content-safe prefix metadata contracts for MemBind v3.1."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from paper_eval.membind_v31.prefix_affinity import (
    MemBindV31PrefixAffinityError,
    PrefixMetadata,
    QwenGraphitiPrefixEncoder,
    PrefixProviderIndex,
)


class _Tokenizer:
    def __init__(self) -> None:
        self.calls: list[tuple[list[dict[str, str]], dict[str, object]]] = []

    def apply_chat_template(self, messages, **kwargs):
        selected = [dict(row) for row in messages]
        self.calls.append((selected, dict(kwargs)))
        # A deterministic fake tokenization with a long shared system prefix.
        content = "|".join(f"{row['role']}:{row['content']}" for row in selected)
        return [ord(char) for char in content]


@dataclass
class _Message:
    role: str
    content: str


class _Inner:
    structured_output_mode = "json_schema"

    @staticmethod
    def _apply_attribute_extraction_preamble(messages, enabled):
        if enabled:
            messages[0].content += "|ATTR"

    @staticmethod
    def _clean_input(value):
        return value.replace("\x00", "")


def test_encoder_projects_the_exact_effective_qwen_chat_request_without_mutating_input() -> None:
    tokenizer = _Tokenizer()
    encoder = QwenGraphitiPrefixEncoder(
        inner=_Inner(),
        tokenizer=tokenizer,
        prefix_match_unit=4,
        tokenizer_identity_sha256="a" * 64,
        cache_identity_sha256="1" * 64,
        trace_hmac_key=b"a" * 32,
        multilingual_instruction=lambda group_id: f"|LANG:{group_id}",
    )
    messages = [_Message("system", "SYS\x00"), _Message("user", "PRIVATE")]

    metadata = encoder(
        messages,
        response_model=None,
        group_id="history-a",
        prompt_name="extract_nodes",
        attribute_extraction=True,
    )

    assert messages[0].content == "SYS\x00"
    assert messages[1].content == "PRIVATE"
    rendered, options = tokenizer.calls[0]
    assert rendered == [
        {"role": "system", "content": "SYS|ATTR|LANG:history-a"},
        {"role": "user", "content": "PRIVATE"},
    ]
    assert options == {
        "tokenize": True,
        "add_generation_prompt": True,
        "enable_thinking": False,
    }
    assert metadata.token_count > 0
    assert metadata.prefix_match_unit == 4
    assert len(metadata.block_prefix_hmac_sha256s) == metadata.token_count // 4
    assert "PRIVATE" not in repr(metadata)


def test_json_object_schema_injection_is_mode_accurate_and_fail_closed() -> None:
    class JsonObjectInner(_Inner):
        structured_output_mode = "json_object"

    class Schema:
        @staticmethod
        def model_json_schema():
            return {"type": "object", "properties": {"answer": {"type": "string"}}}

    tokenizer = _Tokenizer()
    encoder = QwenGraphitiPrefixEncoder(
        inner=JsonObjectInner(),
        tokenizer=tokenizer,
        prefix_match_unit=4,
        tokenizer_identity_sha256="b" * 64,
        cache_identity_sha256="2" * 64,
        trace_hmac_key=b"b" * 32,
        multilingual_instruction=lambda _group_id: "",
    )
    encoder([_Message("system", "SYS"), _Message("user", "QUESTION")], response_model=Schema)
    assert "Respond with a JSON object" in tokenizer.calls[0][0][-1]["content"]

    with pytest.raises(MemBindV31PrefixAffinityError, match="messages_invalid"):
        encoder([], response_model=None)


def _metadata(
    tokens: list[int],
    *,
    unit: int = 4,
    tokenizer_identity: str = "c" * 64,
    trace_key: bytes = b"c" * 32,
    cache_identity: str = "3" * 64,
) -> PrefixMetadata:
    return PrefixMetadata.from_token_ids(
        tokens,
        prefix_match_unit=unit,
        tokenizer_identity_sha256=tokenizer_identity,
        cache_identity_sha256=cache_identity,
        trace_hmac_key=trace_key,
    )


def test_provider_index_uses_only_completed_providers_and_granularity_aligned_lcp() -> None:
    index = PrefixProviderIndex(prefix_match_unit=4)
    candidate = _metadata([1, 2, 3, 4, 5, 6, 7, 9, 10])
    same_two_blocks = _metadata([1, 2, 3, 4, 5, 6, 7, 9, 99])
    one_block = _metadata([1, 2, 3, 4, 8, 8, 8, 8])

    assert index.affinity(candidate) == (0, 0)
    index.register_completed(same_two_blocks, completion_sequence=1)
    assert index.affinity(candidate) == (8, 1)
    index.register_completed(one_block, completion_sequence=2)
    # The longer provider wins even though the shorter provider is newer.
    assert index.affinity(candidate) == (8, 1)
    index.register_completed(same_two_blocks, completion_sequence=3)
    assert index.affinity(candidate) == (8, 3)
    assert candidate.aligned_lcp(one_block) == 4


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tokenizer_identity", "d" * 64),
        ("trace_key", b"different-private-trace-key".ljust(32, b"0")),
        ("cache_identity", "4" * 64),
        ("unit", 8),
    ],
)
def test_provider_index_freezes_full_prefix_and_backend_identity(field, value) -> None:
    index = PrefixProviderIndex(prefix_match_unit=4)
    index.register_completed(_metadata([1, 2, 3, 4]), completion_sequence=1)
    kwargs = {field: value}
    with pytest.raises(
        MemBindV31PrefixAffinityError,
        match="prefix_(identity|match_unit)_mismatch",
    ):
        index.affinity(_metadata([1, 2, 3, 4, 5, 6, 7, 8], **kwargs))


def test_prefix_metadata_is_tamper_resistant_and_rejects_invalid_token_ids() -> None:
    metadata = _metadata([1, 2, 3, 4, 5])
    metadata.verify()
    with pytest.raises(MemBindV31PrefixAffinityError, match="token_id_invalid"):
        PrefixMetadata.from_token_ids(
            [1, -1],
            prefix_match_unit=4,
            tokenizer_identity_sha256="d" * 64,
            cache_identity_sha256="5" * 64,
            trace_hmac_key=b"d" * 32,
        )
    with pytest.raises(MemBindV31PrefixAffinityError, match="prefix_metadata_hash_mismatch"):
        PrefixMetadata(
            token_count=metadata.token_count,
            prefix_match_unit=metadata.prefix_match_unit,
            token_sequence_hmac_sha256=metadata.token_sequence_hmac_sha256,
            block_prefix_hmac_sha256s=metadata.block_prefix_hmac_sha256s,
            tokenizer_identity_sha256=metadata.tokenizer_identity_sha256,
            trace_key_identity_sha256=metadata.trace_key_identity_sha256,
            cache_identity_sha256=metadata.cache_identity_sha256,
            metadata_sha256="0" * 64,
        ).verify()


def test_public_prefix_identities_are_keyed_and_not_cross_run_dictionary_hashes() -> None:
    first = PrefixMetadata.from_token_ids(
        [1, 2, 3, 4],
        prefix_match_unit=4,
        tokenizer_identity_sha256="f" * 64,
        cache_identity_sha256="6" * 64,
        trace_hmac_key=b"first-private-trace-key".ljust(32, b"0"),
    )
    second = PrefixMetadata.from_token_ids(
        [1, 2, 3, 4],
        prefix_match_unit=4,
        tokenizer_identity_sha256="f" * 64,
        cache_identity_sha256="6" * 64,
        trace_hmac_key=b"second-private-trace-key".ljust(32, b"0"),
    )
    assert first.token_sequence_hmac_sha256 != second.token_sequence_hmac_sha256
    assert first.block_prefix_hmac_sha256s != second.block_prefix_hmac_sha256s
    assert first.trace_key_identity_sha256 != second.trace_key_identity_sha256


def test_public_projection_contains_only_hmac_and_public_identity_material() -> None:
    raw_cache_salt = "private-cache-salt"
    metadata = PrefixMetadata.from_token_ids(
        [101, 202, 303, 404],
        prefix_match_unit=4,
        tokenizer_identity_sha256="f" * 64,
        cache_identity_sha256="7" * 64,
        trace_hmac_key=b"private-trace-key".ljust(32, b"0"),
    )

    projection = metadata.public_projection()

    assert set(projection) == {
        "token_count",
        "prefix_match_unit",
        "token_sequence_hmac_sha256",
        "token_prefix_block_hmac_sha256s",
        "tokenizer_identity_sha256",
        "trace_key_identity_sha256",
        "cache_identity_sha256",
        "prefix_metadata_sha256",
    }
    assert raw_cache_salt not in repr(projection)
    assert "token_ids" not in projection
