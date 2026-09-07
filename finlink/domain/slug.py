"""Deterministic slug generation for theses.

A slug is a FILENAME and a link target, so it must be stable: the same reason must
always produce the same slug, or every link in positions.md breaks. That rules out
letting a model invent one — models are free to be creative, and here creativity is
a bug.

Design:
  * prefer the ASCII words already in the reason (stopwords dropped)
  * fall back to the codepoint of CJK ideographs when there is no ASCII at all,
    so Chinese reasons get a stable, distinct slug instead of collapsing to 'thesis'
  * never emit path-unsafe characters
"""

from __future__ import annotations

import re

MAX_WORDS = 5
MAX_LEN = 40

_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-\.%\+]*")

STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "will",
        "with",
        "stock",
        "shares",
        "company",
        "inc",
        "corp",
        "my",
        "me",
        "i",
        "we",
        "you",
        "they",
        "value",
        "price",
        "buy",
        "sell",
        "long",
        "short",
        "term",
        "investment",
        "invest",
        "money",
    ]
)

CJK = re.compile(r"[㐀-鿿豈-﫿]")

# Common particles that carry no meaning in a slug (de/shi/le/...).
CJK_STOPWORDS = frozenset(
    [
        "de", "shi", "le", "zai", "he", "yu", "wo", "ni", "ta", "men",
        "ge", "zhe", "na", "zhi", "you", "bu", "jiu", "hen", "ma", "ne",
        "ba",
    ]
)


def _pinyin(text: str) -> str:
    """Romanise CJK via pypinyin when it is installed.

    Optional on purpose: pinyin makes slugs readable, but it must never be required
    for finlink to run. No pypinyin -> falls back to codepoints.
    """
    try:
        from pypinyin import Style, lazy_pinyin  # type: ignore[import-not-found]
    except ImportError:
        return ""
    syllables = lazy_pinyin(text, style=Style.NORMAL)
    # Only accept clean ASCII syllables: a passthrough of untransliterated CJK would
    # produce a slug that is neither readable nor safe.
    romanised = [
        s.strip().lower()
        for s in syllables
        if s and s.strip() and s.isascii() and s.isalpha()
    ]
    if not romanised:
        return ""
    # Drop filler particles so 'kan-hao-teng-xun-de' becomes 'kan-hao-teng-xun'.
    meaningful = [s for s in romanised if s not in CJK_STOPWORDS]
    chosen = meaningful or romanised
    result = "-".join(chosen[:MAX_WORDS])
    return result or ""


def _cjk_tail(text: str) -> str:
    """Stable ASCII token from CJK characters.

    Prefers pinyin when pypinyin is installed (readable: 'kan-hao-teng-xun');
    otherwise falls back to codepoints ('cjk-770b597d817e'). Either way the result is
    deterministic and unique, which is what a filename needs.
    """
    romanised = _pinyin(text)
    if romanised:
        return romanised
    chars = CJK.findall(text)
    if not chars:
        return ""
    return "cjk-" + "".join(f"{ord(c):x}" for c in chars[:3])


def slugify(text: str, *, max_len: int = MAX_LEN) -> str:
    """Derive a filesystem- and markdown-safe slug from a reason or thesis text."""
    raw = (text or "").strip()
    if not raw:
        return "thesis"

    words = [w.strip(".") for w in _TOKEN.findall(raw)]
    meaningful = [w for w in words if w.lower() not in STOPWORDS]

    if meaningful:
        base = "-".join(meaningful[:MAX_WORDS]).lower()
    else:
        base = _cjk_tail(raw)
        if not base:
            # No ASCII and no CJK: hash it rather than emit an empty slug.
            import hashlib

            base = "thesis-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    base = re.sub(r"[^a-z0-9\-]+", "-", base).strip("-")
    return base[:max_len].strip("-") or "thesis"


SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def is_adoptable(slug: str) -> bool:
    """True when `slug` is safe to use as a filename and link target.

    A model may propose the WORDS, never the slug: this is the gate that keeps
    `finlink improve-slugs` from breaking every link in positions.md.
    """
    return (
        bool(slug)
        and slug.isascii()
        and slug != "thesis"
        and len(slug) <= MAX_LEN
        and SLUG_RE.fullmatch(slug) is not None
    )


def unique_slug(base: str, taken: set[str]) -> str:
    """Make `base` unique against `taken`, deterministically.

    Two positions can carry the same reason (GLD and LUG.ST both said "uncertainty"),
    and `doctor` fails on duplicate slugs — so the second gets a numeric suffix.
    """
    if base not in taken:
        return base
    i = 2
    while f"{base}-{i}" in taken:
        i += 1
    return f"{base}-{i}"


def normalise_for_compare(text: str) -> str:
    """Casefolded, punctuation-stripped form used to spot duplicate reasons."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def is_cjk(text: str) -> bool:
    return bool(CJK.search(text or ""))
