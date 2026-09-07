"""Two-stage relevance matching, stage 1: the deterministic pre-filter.

Cheap and testable, and it keeps the model off the easy 90% of the work: ticker
match, publication date after thesis creation, token overlap with the hypothesis's
observable metric.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from finlink.ingest.base import NewsItem

_TOKEN = re.compile(r"[a-z0-9][a-z0-9\-.%+]+")
# Words that appear in nearly every financial headline and carry no signal.
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
        "q1",
        "q2",
        "q3",
        "q4",
        "yoy",
        "revenue",
        "earnings",
        "results",
        "report",
        "reports",
        "said",
        "says",
        "new",
        "news",
        "market",
        "markets",
        "update",
        "today",
        "week",
        "year",
        "quarter",
    ]
)


def tokenize(text: str) -> set[str]:
    return {t for t in _TOKEN.findall((text or "").lower()) if t not in STOPWORDS}


def published_after(item: NewsItem, since: str) -> bool:
    """Compare on the date prefix only: sources mix ISO dates with full timestamps."""
    return (item.published_at or "")[:10] >= since


def overlap_score(item: NewsItem, observable_metric: str) -> float:
    """How much of the observable metric the item actually mentions.

    Coverage of the metric (|intersection| / |metric|), not Jaccard: a long metric
    with several clauses would otherwise score near zero against a short headline
    that genuinely reports on it. Unrelated items still score 0.
    """
    metric = tokenize(observable_metric)
    if not metric:
        return 0.0
    item_tokens = tokenize(f"{item.title} {item.summary}")
    if not item_tokens:
        return 0.0
    return len(metric & item_tokens) / len(metric)


@dataclass(frozen=True)
class Candidate:
    item: NewsItem
    score: float


def prefilter(
    items: list[NewsItem],
    *,
    ticker: str,
    since: str,
    observable_metric: str,
    min_score: float = 0.10,
    limit: int = 12,
) -> list[Candidate]:
    """Deterministic stage-1 filter. Never calls a model.

    Items are kept only if the ticker matches, the item is published on/after the
    thesis creation date, and there is non-trivial token overlap with the metric.
    """
    out: list[Candidate] = []
    for it in items:
        if it.ticker.upper() != ticker.upper():
            continue
        if not published_after(it, since):
            continue
        score = overlap_score(it, observable_metric)
        if score < min_score:
            continue
        out.append(Candidate(item=it, score=score))
    out.sort(key=lambda c: (-c.score, c.item.published_at))
    return out[:limit]


def metric_of(hypotheses: list[dict[str, str]]) -> str:
    """Combine every observable metric into one matching surface."""
    return " ".join(str(h.get("observable_metric") or "") for h in hypotheses)
