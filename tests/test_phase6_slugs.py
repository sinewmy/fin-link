"""`finlink improve-slugs` — model-proposed names, deterministic slugs.

The invariant under test: a slug is a filename AND a link target, so a model may
choose the WORDS but never the slug. Every rename must move file, frontmatter and
every link together, or `doctor` fails.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from finlink.domain.slug import is_adoptable, slugify
from finlink.llm.schemas import SlugSuggestion

# ------------------------------------------------------------------ adoptability


def test_adoptable_slugs_are_ascii_lowercase_and_hyphenated():
    assert is_adoptable("tencent-fundamentals")
    assert is_adoptable("biotech")


def test_unadoptable_slugs_are_rejected():
    """These would break a filename or a markdown link."""
    for bad in ["", "thesis", "Bad/Name", "has space", "Upper", "xn--80ak", "a" * 41, "-x-"]:
        assert not is_adoptable(bad), bad


def test_suggestion_must_be_romanisable():
    """A non-Latin suggestion cannot produce a readable slug."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SlugSuggestion(keywords="腾讯")


def test_suggestion_accepts_ascii_words():
    s = SlugSuggestion(keywords="tencent fundamentals", rationale="the claim")
    assert slugify(s.keywords) == "tencent-fundamentals"


def test_echo_fixture_names_a_chinese_reason_in_english():
    """The case the user asked about: a CJK slug becomes readable."""
    from finlink.llm.drivers.echo import EchoDriver

    out, _ = EchoDriver().complete(
        system="", user="TICKER: 00700.HK\nREASON: 看好腾讯的基本面\n", schema=SlugSuggestion
    )
    slug = slugify(out.keywords)
    assert slug.isascii() and not slug.startswith("cjk-")
    assert "tencent" in slug or "fundamentals" in slug


# ------------------------------------------------------------------------ fixture


@pytest.fixture()
def seeded(tmp_path: Path) -> Path:
    from finlink.cli import main

    root = tmp_path / "ws"
    CliRunner().invoke(main, ["init", str(root)])
    (root / "portfolio" / "positions.md").write_text(
        "| ticker | quantity | avg_cost | currency | opened_at | thesis_slug | notes |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
        "| 00700.HK | 700 | 322.686 | HKD | 2024-10-01 | - | 看好腾讯的基本面 |\n"
        "| BABA | 139 | 95.89 | USD | 2026-06-29 | - | cheap valuation ai datacenter |\n",
        encoding="utf-8",
    )
    cwd = Path.cwd()
    os.chdir(root)
    try:
        CliRunner().invoke(
            main, ["backfill-theses", "--driver", "echo", "--no-commit"], catch_exceptions=False
        )
    finally:
        os.chdir(cwd)
    return root


def _run(root: Path, *args: str):
    from finlink.cli import main

    cwd = Path.cwd()
    os.chdir(root)
    try:
        return CliRunner().invoke(
            main, ["improve-slugs", "--driver", "echo", "--no-commit", *args],
            catch_exceptions=False,
        )
    finally:
        os.chdir(cwd)


# ----------------------------------------------------------------------- behaviour


def test_dry_run_writes_nothing(seeded: Path):
    before = {p.name for p in (seeded / "theses").glob("*.md")}
    out = _run(seeded, "--only-unreadable", "--dry-run")
    assert out.exit_code == 0, out.output
    assert "would rename" in out.output
    assert {p.name for p in (seeded / "theses").glob("*.md")} == before


def test_rename_moves_file_frontmatter_and_links_together(seeded: Path):
    from finlink.io.markdown import parse_table
    from finlink.workspace import load_workspace

    out = _run(seeded, "--only-unreadable")
    assert out.exit_code == 0, out.output

    ws = load_workspace(seeded)
    slugs = {tf.slug for _p, tf in ws.theses}
    assert not any(s.startswith("cjk-") for s in slugs)

    # filename and frontmatter agree, or doctor's `should be named` warning fires
    for path, tf in ws.theses:
        assert path.name == f"{tf.ticker}-{tf.slug}.md"

    # every link in positions.md resolves to a real file
    rows = parse_table((seeded / "portfolio" / "positions.md").read_text(encoding="utf-8"))
    linked = {r["thesis_slug"] for r in rows if r["thesis_slug"] not in ("", "-")}
    assert linked <= slugs


def test_doctor_passes_after_a_rename(seeded: Path):
    from finlink import doctor as doctor_mod

    _run(seeded, "--only-unreadable")
    assert doctor_mod.run(seeded), "rename left a dangling link or duplicate slug"


def test_rename_is_idempotent(seeded: Path):
    from finlink.workspace import load_workspace

    _run(seeded, "--only-unreadable")
    first = sorted(tf.slug for _p, tf in load_workspace(seeded).theses)
    again = _run(seeded, "--only-unreadable")
    assert again.exit_code == 0 or "no theses to rename" in again.output
    assert sorted(tf.slug for _p, tf in load_workspace(seeded).theses) == first


def test_rename_never_creates_a_duplicate_slug(tmp_path: Path):
    """Two theses with the same words must not collapse onto one filename."""
    from finlink.cli import main
    from finlink.workspace import load_workspace

    root = tmp_path / "ws"
    CliRunner().invoke(main, ["init", str(root)])
    (root / "portfolio" / "positions.md").write_text(
        "| ticker | quantity | avg_cost | currency | opened_at | thesis_slug | notes |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
        "| 00700.HK | 700 | 322.686 | HKD | 2024-10-01 | - | 看好腾讯的基本面 |\n"
        "| 00981.HK | 500 | 75.60 | HKD | 2026-05-20 | - | 看好腾讯的基本面 |\n",
        encoding="utf-8",
    )
    cwd = Path.cwd()
    os.chdir(root)
    try:
        CliRunner().invoke(
            main, ["backfill-theses", "--driver", "echo", "--no-commit"], catch_exceptions=False
        )
        CliRunner().invoke(
            main,
            ["improve-slugs", "--driver", "echo", "--no-commit", "--only-unreadable"],
            catch_exceptions=False,
        )
        slugs = [tf.slug for _p, tf in load_workspace(root).theses]
    finally:
        os.chdir(cwd)
    assert len(slugs) == len(set(slugs)), f"duplicate slugs: {slugs}"


def test_body_content_survives_the_rename(seeded: Path):
    """A rename moves the file; it must not rewrite what the user wrote."""
    from finlink.io.markdown import read_document
    from finlink.workspace import load_workspace

    before = {tf.ticker: read_document(p).body for p, tf in load_workspace(seeded).theses}
    _run(seeded, "--only-unreadable")
    after = {tf.ticker: read_document(p).body for p, tf in load_workspace(seeded).theses}
    assert after == before


def test_only_unreadable_leaves_readable_slugs_alone(seeded: Path):
    from finlink.workspace import load_workspace

    before = {tf.ticker: tf.slug for _p, tf in load_workspace(seeded).theses}
    _run(seeded, "--only-unreadable")
    after = {tf.ticker: tf.slug for _p, tf in load_workspace(seeded).theses}
    assert after["BABA"] == before["BABA"], "a readable ASCII slug must not be churned"


def test_check_reports_missing_sector_mapping(tmp_path: Path):
    from finlink.cli import main

    root = tmp_path / "ws"
    CliRunner().invoke(main, ["init", str(root)])
    (root / "portfolio" / "positions.md").write_text(
        "| ticker | quantity | avg_cost | currency | opened_at | thesis_slug | notes |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
        "| ZZZZ | 10 | 5 | USD | 2026-01-01 | - | - |\n",
        encoding="utf-8",
    )
    cwd = Path.cwd()
    os.chdir(root)
    try:
        out = CliRunner().invoke(main, ["check"], catch_exceptions=False)
    finally:
        os.chdir(cwd)
    assert out.exit_code == 0, out.output
    assert "no sector" in out.output
    assert "ZZZZ" in out.output


def test_check_is_quiet_when_every_holding_is_mapped(tmp_path: Path):
    from finlink.cli import main

    root = tmp_path / "ws"
    CliRunner().invoke(main, ["init", str(root)])
    (root / "portfolio" / "positions.md").write_text(
        "| ticker | quantity | avg_cost | currency | opened_at | thesis_slug | notes |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
        "| BABA | 10 | 5 | USD | 2026-01-01 | - | - |\n",
        encoding="utf-8",
    )
    (root / "config" / "config.yaml").write_text(
        "base_currency: USD\nsectors:\n  BABA: Consumer Discretionary\n"
        "countries:\n  BABA: US\n",
        encoding="utf-8",
    )
    cwd = Path.cwd()
    os.chdir(root)
    try:
        out = CliRunner().invoke(main, ["check"], catch_exceptions=False)
    finally:
        os.chdir(cwd)
    assert out.exit_code == 0, out.output
    assert "no sector" not in out.output


# ------------------------------------------------------------ API key env var


def test_api_key_prefers_the_codex_env_var(monkeypatch: pytest.MonkeyPatch):
    """OPENROUTER_API_KEY_CODEX is this project's key; it must win."""
    from finlink.config import _api_key_from_env, api_key_env_var

    monkeypatch.setenv("OPENROUTER_API_KEY_CODEX", "codex-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "legacy-key")
    assert api_key_env_var() == "OPENROUTER_API_KEY_CODEX"
    assert _api_key_from_env() == "codex-key"


def test_api_key_falls_back_to_the_plain_env_var(monkeypatch: pytest.MonkeyPatch):
    """An existing shell profile using OPENROUTER_API_KEY keeps working."""
    from finlink.config import _api_key_from_env

    monkeypatch.delenv("OPENROUTER_API_KEY_CODEX", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "legacy-key")
    assert _api_key_from_env() == "legacy-key"


def test_missing_key_error_names_the_codex_var(monkeypatch: pytest.MonkeyPatch):
    """The error must tell the user exactly which var to set."""
    from finlink.llm.base import LLMError
    from finlink.llm.drivers.openrouter import OpenRouterDriver

    monkeypatch.delenv("OPENROUTER_API_KEY_CODEX", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(LLMError, match="OPENROUTER_API_KEY_CODEX"):
        OpenRouterDriver("")


def test_config_file_key_still_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from finlink.config import RuntimeConfig

    monkeypatch.setenv("OPENROUTER_API_KEY_CODEX", "from-env")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yaml").write_text("openrouter_api_key: from-file\n")
    assert RuntimeConfig.load(tmp_path).openrouter_api_key == "from-file"
