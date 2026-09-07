"""Phase 1: LLM abstraction, P1 pipeline, and the write-safety contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from finlink.io.markdown import read_document
from finlink.llm.base import LLMRunLog
from finlink.llm.client import LLMClient, load_prompt
from finlink.llm.drivers.echo import EchoDriver
from finlink.llm.pipelines.decompose import (
    DecomposeInput,
    slugify,
)
from finlink.llm.pipelines.decompose import (
    run as decompose_run,
)
from finlink.llm.schemas import ThesisDecomposition

REASON = "I think datacenter capex keeps rising for two years, and NVIDIA keeps its share"


@pytest.fixture()
def client(tmp_path: Path) -> LLMClient:
    return LLMClient(EchoDriver(), LLMRunLog(tmp_path / "logs" / "llm_runs.jsonl"), "echo")


def test_prompt_template_loads_with_version():
    t = load_prompt("decompose_thesis_v1")
    assert t.version == "1"
    assert "hypothesis" in t.body.lower()
    assert t.hash


def test_echo_driver_produces_valid_schema(tmp_path: Path):
    drv = EchoDriver()
    result, meta = drv.complete(
        system="s",
        user=f"TICKER: NVDA\nREASON: {REASON}\n",
        schema=ThesisDecomposition,
        model="echo",
    )
    assert isinstance(result, ThesisDecomposition)
    cores = [h for h in result.hypotheses if h.kind == "core"]
    assert len(cores) == 1
    assert meta.driver == "echo"


def test_echo_reason_extraction_ignores_retry_noise():
    """Regression: retry scaffolding was being echoed into generated content."""
    from finlink.llm.drivers.echo import _extract_reason

    dirty = (
        "TICKER: NVDA\nREASON: capex keeps rising\n"
        "[system: previous output was rejected — fix exactly this error: bad json]"
    )
    assert _extract_reason(dirty) == "capex keeps rising"


def test_pipeline_creates_draft_not_active(tmp_path: Path, client: LLMClient):
    """The model must never silently activate a thesis."""
    result = decompose_run(
        DecomposeInput(ticker="NVDA", reason=REASON, horizon="2y"),
        client=client,
        theses_dir=tmp_path / "theses",
        model="echo",
    )
    doc = read_document(result.path)
    assert doc.frontmatter["status"] == "draft"
    assert doc.frontmatter["ticker"] == "NVDA"
    assert result.path.name.startswith("NVDA-")


def test_reason_stored_verbatim(tmp_path: Path, client: LLMClient):
    result = decompose_run(
        DecomposeInput(ticker="NVDA", reason=REASON),
        client=client,
        theses_dir=tmp_path / "theses",
        model="echo",
    )
    assert REASON in read_document(result.path).body


def test_duplicate_thesis_file_refused(tmp_path: Path, client: LLMClient):
    kwargs = dict(
        inp=DecomposeInput(ticker="NVDA", reason=REASON, slug="fixed"),
        client=client,
        theses_dir=tmp_path / "theses",
        model="echo",
    )
    decompose_run(**kwargs)  # type: ignore[arg-type]
    with pytest.raises(FileExistsError):
        decompose_run(**kwargs)  # type: ignore[arg-type]


def test_llm_run_logged(tmp_path: Path, client: LLMClient):
    decompose_run(
        DecomposeInput(ticker="NVDA", reason=REASON),
        client=client,
        theses_dir=tmp_path / "theses",
        model="echo",
    )
    log = tmp_path / "logs" / "llm_runs.jsonl"
    assert log.exists()
    rec = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert rec["status"] == "ok"
    assert rec["prompt_version"] == "1"
    assert rec["prompt_hash"]
    assert rec["output"] is not None


def test_failed_run_is_logged_and_raises(tmp_path: Path):
    class BrokenDriver:
        name = "broken"

        def complete(self, *, system, user, schema, model=None):
            raise RuntimeError("boom")

    c = LLMClient(BrokenDriver(), LLMRunLog(tmp_path / "logs" / "llm_runs.jsonl"), "broken")  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="failed after 2 attempts"):
        decompose_run(
            DecomposeInput(ticker="NVDA", reason=REASON),
            client=c,
            theses_dir=tmp_path / "theses",
            model="m",
        )
    lines = (tmp_path / "logs" / "llm_runs.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["status"] == "error"
    assert rec["attempts"] == 2
    assert "boom" in (rec["error"] or "")


def test_slugify():
    assert slugify("AI Datacenter Capex Keeps Rising!") == "ai-datacenter-capex-keeps-rising"
    assert slugify("") == "thesis"


def test_frontmatter_matches_workspace_schema(tmp_path: Path, client: LLMClient):
    """The generated file must pass the same model `doctor` uses."""
    from finlink.models import ThesisFrontmatter

    result = decompose_run(
        DecomposeInput(ticker="NVDA", reason=REASON, horizon="2y"),
        client=client,
        theses_dir=tmp_path / "theses",
        model="echo",
    )
    fm = ThesisFrontmatter.model_validate(read_document(result.path).frontmatter)
    assert fm.status == "draft"
    assert fm.hypotheses
    assert all(h.observable_metric for h in fm.hypotheses)


def test_schema_rejects_two_cores():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="exactly 1 core"):
        ThesisDecomposition(
            thesis_statement="x",
            hypotheses=[
                {"id": "h1", "kind": "core", "statement": "a", "observable_metric": "m"},
                {"id": "h2", "kind": "core", "statement": "b", "observable_metric": "n"},
            ],
            invalidation_conditions=["c"],
        )


def test_schema_rejects_unknown_parent():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="unknown parent"):
        ThesisDecomposition(
            thesis_statement="x",
            hypotheses=[
                {"id": "h1", "kind": "core", "statement": "a", "observable_metric": "m"},
                {
                    "id": "h2",
                    "kind": "sub",
                    "parent": "h9",
                    "statement": "b",
                    "observable_metric": "n",
                },
            ],
            invalidation_conditions=["c"],
        )


def test_schema_rejects_vague_invalidation():
    """invalidation_conditions must be observable, not rhetorical."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ThesisDecomposition(
            thesis_statement="x",
            hypotheses=[
                {"id": "h1", "kind": "core", "statement": "a", "observable_metric": "m"},
                {
                    "id": "h2",
                    "kind": "sub",
                    "parent": "h1",
                    "statement": "b",
                    "observable_metric": "n",
                },
            ],
            invalidation_conditions=[],
        )


def test_confirm_gate(tmp_path: Path, client: LLMClient):
    """draft -> active only via an explicit confirm step."""
    from finlink.io.markdown import set_frontmatter_key

    result = decompose_run(
        DecomposeInput(ticker="NVDA", reason=REASON),
        client=client,
        theses_dir=tmp_path / "theses",
        model="echo",
    )
    assert read_document(result.path).frontmatter["status"] == "draft"
    set_frontmatter_key(result.path, "status", "active")
    doc = read_document(result.path)
    assert doc.frontmatter["status"] == "active"
    assert REASON in doc.body  # body untouched by status change


def test_openrouter_driver_requires_api_key():
    """Never silently proceed without credentials."""
    from finlink.llm.base import LLMError
    from finlink.llm.drivers.openrouter import OpenRouterDriver

    with pytest.raises(LLMError, match="API key missing"):
        OpenRouterDriver("")


def test_openrouter_sends_require_parameters(monkeypatch):
    """Without require_parameters=True OpenRouter may route to a schema-ignoring endpoint."""
    import finlink.llm.drivers.openrouter as or_mod
    from finlink.llm.schemas import ThesisDecomposition

    captured: dict = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            payload = ThesisDecomposition(
                thesis_statement="x",
                hypotheses=[
                    {"id": "h1", "kind": "core", "statement": "a", "observable_metric": "m"},
                    {
                        "id": "h2",
                        "kind": "sub",
                        "parent": "h1",
                        "statement": "b",
                        "observable_metric": "n",
                    },
                ],
                invalidation_conditions=["c"],
            )

            msg = type("M", (), {"content": payload.model_dump_json()})()
            choice = type("C", (), {"message": msg})()

            class Resp:
                choices = [choice]
                usage = type("U", (), {"prompt_tokens": 1, "completion_tokens": 2})()

            return Resp()

    class FakeClient:
        chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(or_mod, "OpenAI", lambda **kw: FakeClient())
    drv = or_mod.OpenRouterDriver("test-key")
    drv.complete(system="s", user="u", schema=ThesisDecomposition, model="m")

    assert captured["extra_body"]["provider"]["require_parameters"] is True
    assert captured["response_format"]["json_schema"]["strict"] is True
