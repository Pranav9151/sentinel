"""
News context extraction for Trade Research Cards.

This module prepares bounded, PII-scrubbed context for card narration. It does
not score securities and does not change trade eligibility.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(frozen=True)
class NewsItem:
    title: str
    source: str
    published_at: str
    url: str = ""


@dataclass(frozen=True)
class NewsContext:
    symbol: str
    items: list[NewsItem]
    stale: bool = False
    note: str = ""


def extract_news_context(
    symbol: str,
    raw_articles: Sequence[dict[str, Any]],
    max_items: int = 3,
) -> NewsContext:
    """Extract bounded context from NewsAPI-like article dictionaries."""
    items: list[NewsItem] = []
    for article in raw_articles:
        title = scrub_text(str(article.get("title") or ""))
        if not title:
            continue
        source = article.get("source") or {}
        source_name = scrub_text(str(source.get("name") if isinstance(source, dict) else source))
        items.append(NewsItem(
            title=title[:180],
            source=source_name[:80] or "unknown",
            published_at=str(article.get("publishedAt") or article.get("published_at") or ""),
            url=str(article.get("url") or ""),
        ))
        if len(items) >= max_items:
            break

    return NewsContext(
        symbol=symbol,
        items=items,
        stale=not bool(items),
        note="No recent news context available." if not items else "",
    )


def scrub_text(text: str) -> str:
    """Remove common PII patterns before text can be sent to any LLM provider."""
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[email]", text)
    text = re.sub(r"\+?\d[\d\s().-]{8,}\d", "[phone]", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text
