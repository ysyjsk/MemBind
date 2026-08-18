"""One-time provisioning for the private v3.1 trace-HMAC key."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import tempfile
from pathlib import Path


KEY_NAME = "MEMBIND_V31_TRACE_HMAC_KEY"
_KEY = re.compile(r"^[0-9a-f]{64}$")


def _identity(value: str) -> str:
    if _KEY.fullmatch(value) is None:
        raise ValueError("trace_hmac_key_invalid")
    return hashlib.sha256(bytes.fromhex(value)).hexdigest()


def ensure_private_trace_key(path: Path) -> dict[str, str]:
    """Create a 256-bit key once, returning only its public identity."""

    target = Path(path)
    if target.is_symlink():
        raise ValueError("private_env_symlink_rejected")
    try:
        text = target.read_text(encoding="utf-8") if target.exists() else ""
    except (OSError, UnicodeError):
        raise ValueError("private_env_unreadable") from None
    values = [
        line.split("=", 1)[1].strip()
        for line in text.splitlines()
        if line.split("=", 1)[0].strip() == KEY_NAME and "=" in line
    ]
    if len(values) > 1:
        raise ValueError("trace_hmac_key_duplicate")
    if values:
        identity = _identity(values[0])
        try:
            os.chmod(target, 0o600)
        except OSError:
            raise ValueError("private_env_permission_failed") from None
        return {"disposition": "REUSED", "key_identity_sha256": identity}

    value = secrets.token_hex(32)
    identity = _identity(value)
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    separator = "" if not text or text.endswith("\n") else "\n"
    output = text + separator + f"{KEY_NAME}={value}\n"
    descriptor, temporary = tempfile.mkstemp(
        dir=parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
        text=True,
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(output)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        directory = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return {"disposition": "CREATED", "key_identity_sha256": identity}


__all__ = ["KEY_NAME", "ensure_private_trace_key"]
