"""Resolved runtime config: models per pipeline, API keys, budget caps."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_PIPELINES: dict[str, str] = {}

# OPENROUTER_API_KEY_CODEX is the key this project reads; plain OPENROUTER_API_KEY
# is still honoured so an existing shell profile keeps working.
API_KEY_ENV_VARS = ("OPENROUTER_API_KEY_CODEX", "OPENROUTER_API_KEY")


def api_key_env_var() -> str:
    """Which env var supplied the key. Used in error messages that name the fix."""
    return API_KEY_ENV_VARS[0]


def _api_key_from_env() -> str:
    for var in API_KEY_ENV_VARS:
        value = os.environ.get(var)
        if value:
            return str(value)
    return ""


@dataclass
class RuntimeConfig:
    root: Path
    models: dict[str, str]
    openrouter_api_key: str
    driver: str

    @classmethod
    def load(cls, root: Path) -> RuntimeConfig:
        path = root / "config" / "config.yaml"
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
        raw = raw or {}
        models = dict(DEFAULT_PIPELINES)
        models.update({k: v for k, v in (raw.get("models") or {}).items() if v})
        key = str(raw.get("openrouter_api_key") or _api_key_from_env() or "")
        return cls(
            root=root,
            models=models,
            openrouter_api_key=key,
            driver=str(raw.get("driver") or "openrouter"),
        )

    def model_for(self, pipeline: str) -> str:
        m = self.models.get(pipeline)
        if not m:
            raise RuntimeError(
                f"no model configured for {pipeline}.\n"
                f"Add it to config/config.yaml under `models:`, e.g.\n"
                f"    models:\n      {pipeline}: openai/gpt-4o-mini\n"
                f"(Or run with --driver echo to test offline at zero cost.)"
            )
        return m
