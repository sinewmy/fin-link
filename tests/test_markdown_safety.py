"""The append-only guarantee — the highest-risk area of a markdown-first design."""

from __future__ import annotations

from pathlib import Path

import pytest

from finlink.io.markdown import (
    append_section,
    extract_section,
    parse_table,
    read_document,
    render_document,
    set_frontmatter_key,
)

THESIS = """---
ticker: NVDA
slug: ai-capex
status: active
created: '2026-09-06'
horizon: 2y
invalidation_conditions:
  - hyperscaler capex negative two quarters
confidence: medium
hypotheses:
  - id: h1
    kind: core
    statement: AI datacenter capex keeps growing
    observable_metric: hyperscaler capex guidance
    status: pending
---

## Thesis

I think NVDA keeps growing because AI datacenter capex keeps rising.
"""


@pytest.fixture()
def thesis(tmp_path: Path) -> Path:
    p = tmp_path / "NVDA-ai-capex.md"
    p.write_text(THESIS, encoding="utf-8")
    return p


def test_append_section_preserves_body_byte_for_byte(thesis: Path):
    before = thesis.read_text(encoding="utf-8")
    append_section(thesis, "Validation — 2026-09-06", "### Verdict\nstill_valid")
    after = thesis.read_text(encoding="utf-8")

    assert after.startswith(before), "existing content must be preserved verbatim"
    assert "### Verdict" in after


def test_append_is_idempotent_safe_to_run_twice(thesis: Path):
    append_section(thesis, "Validation — 2026-09-06", "first")
    append_section(thesis, "Validation — 2026-09-07", "second")
    body = thesis.read_text(encoding="utf-8")
    assert "first" in body and "second" in body
    assert extract_section(read_document(thesis).body, "Thesis") is not None


def test_set_frontmatter_key_mutates_only_named_key(thesis: Path):
    before_body = read_document(thesis).body
    set_frontmatter_key(thesis, "status", "challenged")
    doc = read_document(thesis)

    assert doc.frontmatter["status"] == "challenged"
    assert doc.frontmatter["ticker"] == "NVDA"          # untouched
    assert doc.frontmatter["confidence"] == "medium"    # untouched
    assert doc.body == before_body                      # body untouched


def test_set_frontmatter_rejects_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        set_frontmatter_key(tmp_path / "nope.md", "status", "active")


def test_handwritten_body_survives_many_runs(thesis: Path):
    """Phase 3 exit criterion: the hand-written body is byte-identical after validations.

    Note: frontmatter is re-serialised by set_frontmatter_key (YAML quoting may change),
    which is acceptable — the BODY holds the irreplaceable hand-written evidence.
    """
    original_body = read_document(thesis).body
    for i in range(5):
        append_section(thesis, f"Validation — 2026-09-0{i + 1}", f"run {i}")
        set_frontmatter_key(thesis, "status", "active" if i % 2 else "challenged")

    assert read_document(thesis).body.startswith(original_body), (
        "hand-written body must survive all runs unchanged (validations append after it)"
    )
    for i in range(5):
        assert f"run {i}" in read_document(thesis).body


def test_round_trip_preserves_frontmatter(thesis: Path):
    doc = read_document(thesis)
    text = render_document(doc)
    doc2 = read_document_from_text(text)
    assert doc2.frontmatter == doc.frontmatter


def read_document_from_text(text: str):
    from finlink.io.markdown import parse_document

    return parse_document(text)


def test_parse_table_rejects_ragged_rows():
    from finlink.io.markdown import MarkdownError

    bad = "| a | b |\n| --- | --- |\n| 1 |\n"
    with pytest.raises(MarkdownError, match="expected 2"):
        parse_table(bad)


def test_missing_frontmatter_raises():
    from finlink.io.markdown import MarkdownError, parse_document

    with pytest.raises(MarkdownError, match="no YAML frontmatter"):
        parse_document("# just a heading\n")
