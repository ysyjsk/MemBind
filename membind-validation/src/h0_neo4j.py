"""Strict one-shot Neo4j readiness for H0-B and H0-C.

The caller must first pass the H0 state gate and then provide an explicit,
already-bound URI and credentials.  This module never reads project
configuration and never opens a session or executes Cypher.
"""

from __future__ import annotations

from h0_bootstrap import disable_implicit_dotenv

disable_implicit_dotenv()

import hashlib
import inspect
import re
from copy import deepcopy
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from neo4j import AsyncGraphDatabase
from neo4j.exceptions import DriverError

from h0_runtime import H0InfrastructureError, H0ManifestError


_BINDING_KEYS = frozenset({"uri", "user"})
_CREDENTIAL_KEYS = frozenset({"uri", "user", "password"})
_URI_SCHEMES = frozenset(
    {"neo4j", "neo4j+s", "neo4j+ssc", "bolt", "bolt+s", "bolt+ssc"}
)
_ATTEMPT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class H0Neo4jValidationError(H0ManifestError):
    """A sanitized H0 Neo4j binding or readiness contract failure."""


def _fail(reason: str) -> H0Neo4jValidationError:
    return H0Neo4jValidationError(f"H0 Neo4j readiness denied: {reason}")


def _canonical_uri(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise _fail(f"{label}_invalid")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise _fail(f"{label}_invalid") from exc
    if (
        parsed.scheme.lower() not in _URI_SCHEMES
        or not parsed.hostname
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise _fail(f"{label}_not_bound_neo4j_uri")
    hostname = parsed.hostname.lower()
    if ":" in hostname:
        hostname = f"[{hostname}]"
    return f"{parsed.scheme.lower()}://{hostname}:{port}"


def _identifier(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _ATTEMPT_ID_RE.fullmatch(value) is None:
        raise _fail(f"{label}_invalid")
    return value


def _is_infrastructure_failure(exc: Exception) -> bool:
    return isinstance(exc, (DriverError, TimeoutError, ConnectionError, OSError))


class H0Neo4jReadiness:
    """Perform one connectivity-only check and retain only safe evidence."""

    def __init__(
        self,
        *,
        binding: Mapping[str, Any],
        credentials: Mapping[str, Any],
        attempt_id: str,
        candidate: str,
        phase: str,
        driver_factory: Callable[..., Any] = AsyncGraphDatabase.driver,
    ) -> None:
        if not isinstance(binding, Mapping) or set(binding) != _BINDING_KEYS:
            raise _fail("binding_shape_invalid")
        if not isinstance(credentials, Mapping) or set(credentials) != _CREDENTIAL_KEYS:
            raise _fail("credentials_shape_invalid")

        bound_uri = _canonical_uri(binding.get("uri"), label="bound_uri")
        credential_uri = _canonical_uri(
            credentials.get("uri"), label="credential_uri"
        )
        bound_user = binding.get("user")
        credential_user = credentials.get("user")
        password = credentials.get("password")
        if (
            bound_uri != credential_uri
            or not isinstance(bound_user, str)
            or not bound_user
            or credential_user != bound_user
        ):
            raise _fail("uri_or_user_differs_from_binding")
        if not isinstance(password, str) or not password:
            raise _fail("password_missing")
        if candidate not in {"Q1", "Q2", "Q3"}:
            raise _fail("candidate_invalid")
        if phase not in {"H0-B", "H0-C"}:
            raise _fail("phase_invalid")
        if not callable(driver_factory):
            raise _fail("driver_factory_invalid")

        self._uri = bound_uri
        self._user = bound_user
        self._password = password
        self._identity = {
            "attempt_id": _identifier(attempt_id, label="attempt_id"),
            "candidate": candidate,
            "phase": phase,
        }
        self._driver_factory = driver_factory
        self._performed = False
        self._evidence: list[dict[str, Any]] = []

    def _event(
        self,
        *,
        construct_attempts: int,
        verify_calls: int,
        close_calls: int,
        failure_code: str | None,
    ) -> dict[str, Any]:
        return {
            "uri_sha256": hashlib.sha256(self._uri.encode("ascii")).hexdigest(),
            **self._identity,
            "driver_construct_attempt_count": construct_attempts,
            "verify_connectivity_call_count": verify_calls,
            "cypher_call_count": 0,
            "close_call_count": close_calls,
            "readiness_code": "failure" if failure_code else "pass",
            "failure_code": failure_code,
        }

    async def readiness(self) -> dict[str, Any]:
        """Verify connectivity once, execute no Cypher, and always try to close."""

        if self._performed:
            raise _fail("readiness_already_performed")
        self._performed = True

        driver: Any = None
        construct_attempts = 1
        verify_calls = 0
        close_calls = 0
        failure_code: str | None = None
        failure: Exception | None = None
        try:
            driver = self._driver_factory(
                self._uri,
                auth=(self._user, self._password),
            )
            if not callable(getattr(driver, "verify_connectivity", None)):
                raise TypeError("driver verify_connectivity contract missing")
            if not callable(getattr(driver, "close", None)):
                raise TypeError("driver close contract missing")
            verify_calls = 1
            result = driver.verify_connectivity()
            if not inspect.isawaitable(result):
                raise TypeError("driver verify_connectivity must be awaitable")
            await result
        except Exception as exc:
            failure = exc
            failure_code = (
                "neo4j_unreachable"
                if _is_infrastructure_failure(exc)
                else "neo4j_readiness_contract_failure"
            )
        finally:
            if driver is not None and callable(getattr(driver, "close", None)):
                close_calls = 1
                try:
                    close_result = driver.close()
                    if inspect.isawaitable(close_result):
                        await close_result
                except Exception:
                    pass

        event = self._event(
            construct_attempts=construct_attempts,
            verify_calls=verify_calls,
            close_calls=close_calls,
            failure_code=failure_code,
        )
        self._evidence.append(deepcopy(event))
        if failure_code == "neo4j_unreachable":
            raise H0InfrastructureError(
                "neo4j_unreachable: stop_and_report"
            ) from failure
        if failure_code is not None:
            raise _fail(failure_code) from failure
        return deepcopy(event)

    def safe_evidence(self) -> list[dict[str, Any]]:
        """Return a defensive copy of the sanitized readiness event."""

        return deepcopy(self._evidence)
