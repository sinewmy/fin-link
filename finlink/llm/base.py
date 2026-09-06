"""LLM driver interface and run logging."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

S = TypeVar("S", bound=BaseModel)


@dataclass
class LLMMetadata:
    driver: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class LLMDriver(Protocol):
    """Every driver returns a validated model instance. Never raw text."""

    name: str

    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: type[S],
        model: str | None = None,
    ) -> tuple[S, LLMMetadata]: ...


class LLMError(RuntimeError):
    """Raised when a call fails permanently. Never fall back to unvalidated text."""


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass
class LLMRunLog:
    path: Path

    def append(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def record(
        self,
        *,
        pipeline: str,
        driver: str,
        model: str,
        prompt_version: str,
        prompt_hash: str,
        input_hash: str,
        status: str,
        output: BaseModel | None = None,
        meta: LLMMetadata | None = None,
        error: str | None = None,
        attempts: int = 1,
    ) -> None:
        self.append(
            {
                "ts": datetime.now(UTC).isoformat(),
                "pipeline": pipeline,
                "driver": driver,
                "model": model,
                "prompt_version": prompt_version,
                "prompt_hash": prompt_hash,
                "input_hash": input_hash,
                "status": status,
                "attempts": attempts,
                "tokens_in": meta.tokens_in if meta else 0,
                "tokens_out": meta.tokens_out if meta else 0,
                "cost_usd": meta.cost_usd if meta else 0.0,
                "latency_ms": meta.latency_ms if meta else 0,
                "output": output.model_dump(mode="json") if output else None,
                "error": error,
            }
        )


def _now_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
