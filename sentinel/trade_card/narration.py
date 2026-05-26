"""
Trade Research Card narration.

This deterministic local narration intentionally restates only structured card
fields. It does not add predictions or external claims.
"""

from __future__ import annotations

from sentinel.trade_card.builder import TradeResearchCard
from sentinel.trade_card.news_extractor import NewsContext


def narrate_card(card: TradeResearchCard, news_context: NewsContext | None = None) -> str:
    lines = []
    if card.amber_banner:
        lines.append(card.amber_banner)
    lines.extend([
        (
            f"{card.symbol} is a {card.card_type.replace('_', '-')} research card "
            f"from {card.screener} with conviction {card.conviction_score:.0f}/100."
        ),
        (
            f"The planned entry zone is {card.entry_low:.2f} to {card.entry_high:.2f}, "
            f"with stop {card.stop_loss:.2f} and target {card.target_1:.2f}."
        ),
        (
            f"Risk/reward is 1:{card.risk_reward:.2f}; suggested quantity is "
            f"{card.suggested_quantity}, placing about {card.risk_amount_inr:.2f} INR at risk."
        ),
        (
            f"Estimated round-trip cost is {card.estimated_round_trip_cost_inr:.2f} INR. "
            f"{card.estimated_tax_note}"
        ),
    ])
    if card.warnings:
        lines.append("Warnings: " + "; ".join(card.warnings))
    if news_context and news_context.items:
        lines.append(
            "News context: "
            + "; ".join(f"{item.source}: {item.title}" for item in news_context.items)
        )
    return "\n".join(lines)
