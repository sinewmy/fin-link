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

# Alpha Vantage free-tier key. Same .env convention.
ALPHAVANTAGE_API_KEY_ENV_VAR = "ALPHAVANTAGE_API_KEY"
# Optional second key; used round-robin so one key's daily cap doesn't stall a run.
ALPHAVANTAGE_API_KEY_2_ENV_VARS = ("ALPHAVANTAGE_API_KEY_2", "ALPHAVANTAGE_API_KEY-2")


def _load_dotenv(root: Path) -> None:
    """Load `KEY=value` lines from a gitignored .env into os.environ (no overwrite).

    A minimal loader: no external dependency, no shell expansion. Values already
    exported in the environment take precedence over .env.
    """
    env_path = root / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


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
    alphavantage_api_key: str
    alphavantage_api_key_2: str
    driver: str

    @classmethod
    def load(cls, root: Path) -> RuntimeConfig:
        _load_dotenv(root)
        path = root / "config" / "config.yaml"
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
        raw = raw or {}
        models = dict(DEFAULT_PIPELINES)
        models.update({k: v for k, v in (raw.get("models") or {}).items() if v})
        key = str(raw.get("openrouter_api_key") or _api_key_from_env() or "")
        av_key = str(
            raw.get("alphavantage_api_key")
            or os.environ.get(ALPHAVANTAGE_API_KEY_ENV_VAR)
            or ""
        )
        av_key_2 = str(
            raw.get("alphavantage_api_key_2")
            or next(
                (os.environ[v] for v in ALPHAVANTAGE_API_KEY_2_ENV_VARS if os.environ.get(v)),
                "",
            )
        )
        return cls(
            root=root,
            models=models,
            openrouter_api_key=key,
            alphavantage_api_key=av_key,
            alphavantage_api_key_2=av_key_2,
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
