"""
Grounded LLM narration helpers for Trade Research Cards.

The prompt explicitly forbids new price predictions and restricts the model to
restating supplied structured fields/news context. Calling an external provider
is intentionally left to orchestration code with real credentials.
"""

from __future__ import annotations

from dataclasses import dataclass

from sentinel.trade_card.builder import TradeResearchCard
from sentinel.trade_card.news_extractor import NewsContext

FORBIDDEN_SIGNAL_PHRASES = (
    "will rise",
    "will fall",
    "guaranteed",
    "sure shot",
    "must buy",
    "must sell",
    "price target upgraded by me",
)


@dataclass(frozen=True)
class GroundedNarrationPrompt:
    system: str
    user: str


def build_grounded_narration_prompt(
    card: TradeResearchCard,
    news_context: NewsContext | None = None,
) -> GroundedNarrationPrompt:
    """Build a provider-neutral prompt for card narration."""
    news_lines = []
    if news_context:
        for item in news_context.items:
            news_lines.append(f"- {item.published_at} | {item.source}: {item.title}")
        if news_context.note:
            news_lines.append(f"- {news_context.note}")

    user = "\n".join([
        "Create a concise operator narration for this Trade Research Card.",
        "Use only the supplied fields. Do not add predictions or advice.",
        "",
        f"Symbol: {card.symbol}",
        f"Card type: {card.card_type}",
        f"Screener: {card.screener}",
        f"Direction: {card.direction}",
        f"Conviction score: {card.conviction_score:.0f}/100",
        f"Entry zone: {card.entry_low:.4f} to {card.entry_high:.4f}",
        f"Stop loss: {card.stop_loss:.4f}",
        f"Target 1: {card.target_1:.4f}",
        f"Risk reward: 1:{card.risk_reward:.2f}",
        f"Suggested quantity: {card.suggested_quantity}",
        f"Risk INR: {card.risk_amount_inr:.2f}",
        f"Round-trip cost INR: {card.estimated_round_trip_cost_inr:.2f}",
        f"Tax note: {card.estimated_tax_note}",
        f"Amber banner: {card.amber_banner or 'none'}",
        f"Warnings: {'; '.join(card.warnings) if card.warnings else 'none'}",
        "",
        "News context:",
        *(news_lines or ["- none"]),
    ])
    return GroundedNarrationPrompt(
        system=(
            "You narrate Sentinel Trade Research Cards. You must not originate "
            "signals, add new numeric claims, or predict prices. Every claim must "
            "trace to the supplied structured fields or news lines."
        ),
        user=user,
    )


def validate_narration_grounding(text: str) -> list[str]:
    """Return grounding violations found in generated narration text."""
    lower = text.lower()
    return [phrase for phrase in FORBIDDEN_SIGNAL_PHRASES if phrase in lower]
