"""Content-safe request profiles for v4 resource admission.

Profiles contain only request metadata already available to the adapter.  No
prompt text, token IDs, or response content is retained here.  The
classification rule is deliberately deterministic so candidate policy files
can be replayed offline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class ResourceProfileError(ValueError):
    """A resource profile is malformed or contains unsafe data."""


class ResourceClass(str, Enum):
    LONG_PREFILL = "LONG_PREFILL"
    MIXED = "MIXED"
    SHORT = "SHORT"


class Criticality(str, Enum):
    FRONTIER = "FRONTIER"
    BACKGROUND = "BACKGROUND"


def _fail(code: str) -> ResourceProfileError:
    return ResourceProfileError(code)


def _identity(value: object, code: str) -> str:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        raise _fail(code)
    return value


def _nonnegative_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


@dataclass(frozen=True, slots=True)
class RequestProfile:
    """The minimum content-free metadata needed by an admission policy."""

    request_id: str
    prompt_name: str
    prompt_tokens_estimate: int
    expected_output_tokens: int
    resource_class: ResourceClass
    criticality: Criticality
    source_sequence: int
    state_version: int
    exact_prefix_tokens: int
    execution_mode: str = "LLM"

    @property
    def prompt_tokens(self) -> int:
        """Compatibility alias used by request telemetry adapters."""

        return self.prompt_tokens_estimate

    @property
    def expected_output(self) -> int:
        return self.expected_output_tokens

    def __post_init__(self) -> None:
        _identity(self.request_id, "request_id_invalid")
        _identity(self.prompt_name, "prompt_name_invalid")
        _nonnegative_int(self.prompt_tokens_estimate, "prompt_tokens_invalid")
        _nonnegative_int(self.expected_output_tokens, "output_tokens_invalid")
        if not isinstance(self.resource_class, ResourceClass):
            raise _fail("resource_class_invalid")
        if not isinstance(self.criticality, Criticality):
            raise _fail("criticality_invalid")
        _nonnegative_int(self.source_sequence, "source_sequence_invalid")
        _nonnegative_int(self.state_version, "state_version_invalid")
        _nonnegative_int(self.exact_prefix_tokens, "exact_prefix_tokens_invalid")
        if self.execution_mode not in {"LLM", "NO_LLM"}:
            raise _fail("execution_mode_invalid")

    def public_dict(self) -> dict[str, object]:
        """Return fields suitable for content-safe telemetry."""

        return {
            "request_id": self.request_id,
            "prompt_name": self.prompt_name,
            "prompt_tokens_estimate": self.prompt_tokens_estimate,
            "expected_output_tokens": self.expected_output_tokens,
            "resource_class": self.resource_class.value,
            "criticality": self.criticality.value,
            "source_sequence": self.source_sequence,
            "state_version": self.state_version,
            "exact_prefix_tokens": self.exact_prefix_tokens,
            "execution_mode": self.execution_mode,
        }


def classify_request_profile(
    *,
    request_id: str,
    prompt_name: str,
    prompt_tokens: int,
    expected_output_tokens: int,
    source_sequence: int,
    state_version: int,
    exact_prefix_tokens: int,
    long_prefill_cutoff: int = 4096,
    long_decode_cutoff: int = 256,
    execution_mode: str = "LLM",
    criticality: Criticality = Criticality.BACKGROUND,
) -> RequestProfile:
    """Classify a request using the pre-registered role profile rule.

    ``LONG_PREFILL`` means a prompt at or above the frozen cutoff with a short
    decode.  Prompts below that cutoff are ``SHORT``; the remaining requests
    are ``MIXED``.  Callers must freeze
    cutoffs in their candidate artifact before using this helper.
    """

    tokens = _nonnegative_int(prompt_tokens, "prompt_tokens_invalid")
    output = _nonnegative_int(expected_output_tokens, "output_tokens_invalid")
    cutoff = _nonnegative_int(long_prefill_cutoff, "long_prefill_cutoff_invalid")
    decode_cutoff = _nonnegative_int(long_decode_cutoff, "long_decode_cutoff_invalid")
    if cutoff == 0:
        raise _fail("long_prefill_cutoff_invalid")
    if tokens < cutoff:
        category = ResourceClass.SHORT
    elif tokens >= cutoff and output < decode_cutoff:
        category = ResourceClass.LONG_PREFILL
    else:
        category = ResourceClass.MIXED
    return RequestProfile(
        request_id=request_id,
        prompt_name=prompt_name,
        prompt_tokens_estimate=tokens,
        expected_output_tokens=output,
        resource_class=category,
        criticality=criticality,
        source_sequence=source_sequence,
        state_version=state_version,
        exact_prefix_tokens=exact_prefix_tokens,
        execution_mode=execution_mode,
    )


__all__ = [
    "Criticality",
    "RequestProfile",
    "ResourceClass",
    "ResourceProfileError",
    "classify_request_profile",
]
