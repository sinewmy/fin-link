"""Slug generation and the backfill-theses bridge."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from finlink.domain.slug import slugify, unique_slug

# --------------------------------------------------------------------------- slug


def test_slug_is_stable_across_calls():
    """A slug is a filename and a link target — it must never vary."""
    text = "AI datacenter capex keeps rising"
    assert slugify(text) == slugify(text)


def test_slug_drops_stopwords_and_is_lowercase():
    assert slugify("the value is and low and its development") == "low-development"


def test_chinese_reason_gets_a_distinct_slug_not_thesis():
    """Regression: the old rule collapsed every non-ASCII reason to 'thesis'."""
    a = slugify("看好腾讯的基本面")
    b = slugify("看好生物制药的方向")
    assert a != b
    assert a != "thesis" and b != "thesis"


def test_empty_and_punctuation_only_reasons_never_yield_empty():
    assert slugify("") == "thesis"
    assert slugify("???") != ""


def test_slug_is_filesystem_safe():
    for text in ["a/b\\c:d", "hello  world!!", "  ", "中文 test/x"]:
        s = slugify(text)
        assert s and "/" not in s and "\\" not in s and ":" not in s


def test_unique_slug_avoids_collisions():
    assert unique_slug("uncertainty", set()) == "uncertainty"
    assert unique_slug("uncertainty", {"uncertainty"}) == "uncertainty-2"
    assert unique_slug("uncertainty", {"uncertainty", "uncertainty-2"}) == "uncertainty-3"


# ----------------------------------------------------------------------- backfill


@pytest.fixture()
def seeded(tmp_path: Path) -> Path:
    from click.testing import CliRunner

    from finlink.cli import main

    root = tmp_path / "ws"
    CliRunner().invoke(main, ["init", str(root)])
    (root / "portfolio" / "positions.md").write_text(
        "| ticker | quantity | avg_cost | currency | opened_at | thesis_slug | notes |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
        "| BABA | 139 | 95.89 | USD | 2026-06-29 | - | cheap and AI data center growth |\n"
        "| GLD | 24 | 397.042 | USD | 2026-08-14 | - | uncertainty hedge |\n"
        "| LUG.ST | 159 | 628 | SEK | 2026-08-01 | - | uncertainty hedge |\n"
        "| NOPOS | 10 | 5 | USD | 2026-01-01 | - | - |\n",
        encoding="utf-8",
    )
    return root


def test_backfill_creates_one_draft_per_position(seeded: Path):
    from click.testing import CliRunner

    from finlink.cli import main

    cwd = Path.cwd()
    os.chdir(seeded)
    try:
        res = CliRunner().invoke(
            main, ["backfill-theses", "--driver", "echo", "--no-commit"], catch_exceptions=False
        )
        assert res.exit_code == 0, res.output
        theses = sorted(p.name for p in (seeded / "theses").glob("*.md"))
        assert len(theses) == 3, theses  # NOPOS has no reason, so it is skipped
        assert any(t.startswith("BABA-") for t in theses)
        # duplicate reason still yields distinct files
        gld = [t for t in theses if t.startswith("GLD-")]
        lug = [t for t in theses if t.startswith("LUG.ST-")]
        assert len(gld) == 1 and len(lug) == 1
        assert gld[0][4:] != lug[0][7:]
    finally:
        os.chdir(cwd)


def test_backfill_links_slugs_back_into_positions(seeded: Path):
    from click.testing import CliRunner

    from finlink.cli import main
    from finlink.workspace import load_workspace

    cwd = Path.cwd()
    os.chdir(seeded)
    try:
        CliRunner().invoke(
            main, ["backfill-theses", "--driver", "echo", "--no-commit"], catch_exceptions=False
        )
        ws = load_workspace(seeded)
        by_ticker = {p.ticker: p.thesis_slug for p in ws.positions}
        assert by_ticker["BABA"], "slug was not written back"
        assert by_ticker["NOPOS"] in (None, "-"), "a position with no reason must stay unlinked"
        slugs = {tf.slug for _p, tf in ws.theses}
        assert by_ticker["BABA"] in slugs
    finally:
        os.chdir(cwd)


def test_backfilled_theses_are_drafts_and_doctor_passes(seeded: Path):
    """Drafts must not be auto-activated, and links must resolve."""
    from click.testing import CliRunner

    from finlink.cli import main
    from finlink.workspace import load_workspace

    cwd = Path.cwd()
    os.chdir(seeded)
    try:
        CliRunner().invoke(
            main, ["backfill-theses", "--driver", "echo", "--no-commit"], catch_exceptions=False
        )
        ws = load_workspace(seeded)
        assert all(tf.status.value == "draft" for _p, tf in ws.theses)
        doctor = CliRunner().invoke(main, ["doctor"], catch_exceptions=False)
        assert doctor.exit_code == 0, doctor.output
    finally:
        os.chdir(cwd)


def test_backfill_dry_run_writes_nothing(seeded: Path):
    from click.testing import CliRunner

    from finlink.cli import main

    cwd = Path.cwd()
    os.chdir(seeded)
    try:
        res = CliRunner().invoke(
            main, ["backfill-theses", "--driver", "echo", "--dry-run"], catch_exceptions=False
        )
        assert res.exit_code == 0, res.output
        assert "would create" in res.output
        assert not (seeded / "theses").exists() or not list((seeded / "theses").glob("*.md"))
    finally:
        os.chdir(cwd)


def test_backfill_is_idempotent_when_slugs_taken(seeded: Path):
    """Running twice must not collide: the second run sees the first's slugs."""
    from click.testing import CliRunner

    from finlink.cli import main
    from finlink.workspace import load_workspace

    cwd = Path.cwd()
    os.chdir(seeded)
    try:
        runner = CliRunner()
        runner.invoke(
            main, ["backfill-theses", "--driver", "echo", "--no-commit"], catch_exceptions=False
        )
        first = len(list((seeded / "theses").glob("*.md")))
        runner.invoke(
            main, ["backfill-theses", "--driver", "echo", "--no-commit"], catch_exceptions=False
        )
        ws = load_workspace(seeded)
        slugs = [tf.slug for _p, tf in ws.theses]
        assert len(slugs) == len(set(slugs)), "duplicate slugs would fail doctor"
        assert len(list((seeded / "theses").glob("*.md"))) >= first
    finally:
        os.chdir(cwd)


def test_backfill_preserves_other_cells(seeded: Path):
    """_link_slugs must touch only the thesis_slug cell."""
    from click.testing import CliRunner

    from finlink.cli import main
    from finlink.io.markdown import parse_table

    cwd = Path.cwd()
    os.chdir(seeded)
    try:
        before = parse_table((seeded / "portfolio" / "positions.md").read_text())
        CliRunner().invoke(
            main, ["backfill-theses", "--driver", "echo", "--no-commit"], catch_exceptions=False
        )
        after = parse_table((seeded / "portfolio" / "positions.md").read_text())
        for b, a in zip(before, after, strict=True):
            assert b["ticker"] == a["ticker"]
            assert b["quantity"] == a["quantity"]
            assert b["avg_cost"] == a["avg_cost"]
            assert b["opened_at"] == a["opened_at"]
            assert b["notes"] == a["notes"], "notes must survive linking"
    finally:
        os.chdir(cwd)


def test_decompose_uses_the_shared_slug_rule():
    """Both paths must agree, or links break between them."""
    from finlink.domain.slug import slugify as domain_slugify
    from finlink.llm.pipelines.decompose import slugify as pipeline_slugify

    for text in ["AI datacenter capex rising", "看好腾讯的基本面", ""]:
        assert pipeline_slugify(text) == domain_slugify(text)


# ------------------------------------------------------------------- pinyin path


class _FakePinyin:
    """Minimal pypinyin stand-in so the optional path is actually exercised."""

    TABLE = {
        "看": "kan", "好": "hao", "腾": "teng", "讯": "xun", "的": "de",
        "基": "ji", "本": "ben", "面": "mian", "觉": "jue", "得": "de",
        "小": "xiao", "米": "mi", "当": "dang", "时": "shi", "股": "gu",
        "价": "jia", "被": "bei", "低": "di", "估": "gu",
    }

    class Style:
        NORMAL = "normal"

    @classmethod
    def install(cls, monkeypatch: object) -> None:
        import sys
        import types

        mod = types.ModuleType("pypinyin")
        mod.Style = cls.Style  # type: ignore[attr-defined]
        mod.lazy_pinyin = lambda text, style=None: [  # type: ignore[attr-defined]
            cls.TABLE.get(ch, ch) for ch in text if ch.strip()
        ]
        sys.modules["pypinyin"] = mod

    @classmethod
    def remove(cls) -> None:
        import sys

        sys.modules.pop("pypinyin", None)


def test_pinyin_slug_is_readable_when_pypinyin_installed():
    _FakePinyin.install(None)
    try:
        assert slugify("看好腾讯的基本面") == "kan-hao-teng-xun-ji"
        assert slugify("觉得小米当时的股价被低估") == "jue-xiao-mi-dang-gu"
    finally:
        _FakePinyin.remove()


def test_pinyin_drops_particles():
    """'de' is a particle; keeping it makes slugs noisy."""
    _FakePinyin.install(None)
    try:
        out = slugify("看好腾讯的")
        assert out == "kan-hao-teng-xun"
    finally:
        _FakePinyin.remove()


def test_falls_back_to_codepoints_without_pypinyin():
    """pypinyin is OPTIONAL: finlink must still produce valid slugs without it."""
    import sys

    sys.modules.pop("pypinyin", None)
    a, b = slugify("看好腾讯的基本面"), slugify("觉得小米当时的股价被低估")
    assert a.startswith("cjk-") and b.startswith("cjk-")
    assert a != b


def test_pinyin_falls_back_when_transliteration_yields_nothing():
    """A stub returning raw CJK must not leak non-ASCII into a slug."""
    import sys
    import types

    mod = types.ModuleType("pypinyin")

    class Style:
        NORMAL = "normal"

    mod.Style = Style  # type: ignore[attr-defined]
    mod.lazy_pinyin = lambda text, style=None: list(text)  # type: ignore[attr-defined]
    sys.modules["pypinyin"] = mod
    try:
        out = slugify("看好腾讯")
        assert out.startswith("cjk-"), out
        assert out.isascii()
    finally:
        sys.modules.pop("pypinyin", None)


def test_slug_is_always_ascii_and_safe():
    for text in ["看好腾讯的基本面", "BABA cheap valuation", "mixed 中文 and english", ""]:
        s = slugify(text)
        assert s and s.isascii(), f"{text!r} -> {s!r}"
