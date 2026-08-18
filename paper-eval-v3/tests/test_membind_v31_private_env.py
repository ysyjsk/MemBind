"""Private trace-key provisioning never exposes or silently rotates secrets."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from paper_eval.membind_v31.private_env import ensure_private_trace_key


def test_missing_trace_key_is_created_once_without_returning_secret(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("EXISTING=value\n", encoding="utf-8")

    first = ensure_private_trace_key(path)
    content = path.read_text(encoding="utf-8")
    match = re.search(r"^MEMBIND_V31_TRACE_HMAC_KEY=([0-9a-f]{64})$", content, re.M)

    assert first["disposition"] == "CREATED"
    assert match is not None
    assert first["key_identity_sha256"] == hashlib.sha256(
        bytes.fromhex(match.group(1))
    ).hexdigest()
    assert match.group(1) not in repr(first)
    assert path.stat().st_mode & 0o777 == 0o600

    second = ensure_private_trace_key(path)
    assert second["disposition"] == "REUSED"
    assert second["key_identity_sha256"] == first["key_identity_sha256"]
    assert path.read_text(encoding="utf-8") == content


@pytest.mark.parametrize("value", ("00", "z" * 64))
def test_existing_invalid_trace_key_fails_without_rewriting(
    tmp_path: Path, value: str
) -> None:
    path = tmp_path / ".env"
    original = f"MEMBIND_V31_TRACE_HMAC_KEY={value}\n"
    path.write_text(original, encoding="utf-8")

    with pytest.raises(ValueError, match="trace_hmac_key_invalid"):
        ensure_private_trace_key(path)

    assert path.read_text(encoding="utf-8") == original


def test_duplicate_trace_key_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "MEMBIND_V31_TRACE_HMAC_KEY=" + "a" * 64 + "\n"
        "MEMBIND_V31_TRACE_HMAC_KEY=" + "b" * 64 + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="trace_hmac_key_duplicate"):
        ensure_private_trace_key(path)
