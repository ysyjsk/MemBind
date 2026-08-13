"""Transport-independent judge backend contract."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class BackendStatus(str, Enum):
    SUCCESS = "SUCCESS"
    SERVICE_ERROR = "SERVICE_ERROR"


@dataclass(frozen=True)
class JudgeBackendResult:
    """One raw backend outcome with sanitized failure bookkeeping."""

    status: BackendStatus
    raw_output: str | None
    retry_count: int
    error_class: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, BackendStatus):
            raise TypeError("status must be BackendStatus")
        if (
            isinstance(self.retry_count, bool)
            or not isinstance(self.retry_count, int)
            or self.retry_count < 0
        ):
            raise ValueError("retry_count must be a non-negative integer")
        if self.status is BackendStatus.SUCCESS:
            if not isinstance(self.raw_output, str) or self.error_class is not None:
                raise ValueError("successful backend result is inconsistent")
        elif (
            self.raw_output is not None
            or not isinstance(self.error_class, str)
            or not self.error_class
        ):
            raise ValueError("service-error backend result is inconsistent")

    @classmethod
    def success(cls, *, raw_output: str, retry_count: int) -> "JudgeBackendResult":
        return cls(
            status=BackendStatus.SUCCESS,
            raw_output=raw_output,
            retry_count=retry_count,
            error_class=None,
        )

    @classmethod
    def service_error(
        cls, *, retry_count: int, error_class: str
    ) -> "JudgeBackendResult":
        return cls(
            status=BackendStatus.SERVICE_ERROR,
            raw_output=None,
            retry_count=retry_count,
            error_class=error_class,
        )


class JudgeBackend(Protocol):
    model: str
    config_hash: str

    async def judge(self, prompt: str) -> JudgeBackendResult: ...
