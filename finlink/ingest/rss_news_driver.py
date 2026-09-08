"""RSS news driver (feedparser).

Pulls company news from public RSS feeds (no API key) and normalises them into
`NewsItem`s for the `data/news/` cache. The validate pipeline consumes these as
evidence. Items without a resolvable URL or publication date are discarded by the
store's provenance rule, so we fill both faithfully.

The driver is wired to the Alpha Vantage market driver so ingestion also refreshes
news, replacing the mock-only news of earlier phases.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Iterable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import feedparser  # type: ignore[import-untyped]

from finlink.ingest.base import NewsItem

# Ticker -> list of RSS feed URLs (company IR/PR + major financial wires).
# Google News RSS needs no key and is not blocked by Yahoo's aggressive 429
# rate-limiting on this machine:
#   https://news.google.com/rss/search?q=<QUERY>&hl=en-US&gl=US&ceid=US:en
DEFAULT_FEED_TEMPLATES = [
    "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en",
]

# Short/generic tickers that Google's token query does not disambiguate on its
# own ("BABA" -> the religious figure, "LUG.ST" -> football). Mapping the ticker
# to a well-known company name keeps the feed relevant.
TICKER_QUERIES: dict[str, str] = {
    "BABA": "Alibaba",
    "GLD": "SPDR Gold Shares ETF",
    "SGOV": "iShares 0-3 Month Treasury",
    "00700.HK": 'Tencent "00700"',
    "00981.HK": "SMIC Semiconductor",
    "01810.HK": "Xiaomi",
    "02359.HK": "WuXi AppTec",
    "INVE-B.ST": "Investor AB",
    "LUG.ST": "Lundin Gold",
}

def _published_at(entry: Any) -> str:
    """Best-effort ISO timestamp from an RSS entry; falls back to empty."""
    for key in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, key, None)
        if parsed:
            try:
                dt = datetime(*parsed[:6], tzinfo=UTC)
                return dt.isoformat()
            except (ValueError, TypeError):
                continue
    # raw string form as a last resort
    raw = getattr(entry, "published", "") or getattr(entry, "updated", "")
    if raw:
        try:
            return parsedate_to_datetime(raw).astimezone(UTC).isoformat()
        except (ValueError, TypeError):
            pass
    return ""


class RSSNewsDriver:
    name = "rss"

    def __init__(self, feeds: Iterable[str] | None = None, timeout: float = 20.0) -> None:
        # A fixed default template plus any per-ticker extras; see fetch_news.
        self._templates = list(feeds) if feeds is not None else DEFAULT_FEED_TEMPLATES
        self._timeout = timeout

    def _urls(self, ticker: str) -> list[str]:
        query = TICKER_QUERIES.get(ticker.strip().upper(), ticker.strip())
        urls: list[str] = []
        for tpl in self._templates:
            urls.append(tpl.format(query=urllib.parse.quote_plus(query)))
        # dedupe preserving order
        seen: set[str] = set()
        out: list[str] = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                out.append(u)
        return out

    def fetch_news(self, ticker: str, limit: int = 30) -> list[NewsItem]:
        items: list[NewsItem] = []
        for url in self._urls(ticker):
            try:
                feed = feedparser.parse(url)
            except Exception:  # noqa: BLE001 - a bad feed must not abort the run
                continue
            for entry in feed.entries[:limit]:
                link = getattr(entry, "link", "")
                title = getattr(entry, "title", "")
                summary = getattr(entry, "summary", "")
                if not link or not title:
                    continue
                items.append(
                    NewsItem(
                        ticker=ticker,
                        title=title,
                        url=link,
                        published_at=_published_at(entry),
                        source=str(getattr(feed, "feed", {}).get("title", "") or url),
                        summary=summary,
                    )
                )
        # newest first
        items.sort(key=lambda it: it.published_at, reverse=True)
        return items[:limit]
