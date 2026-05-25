"""
Structured Trade Research Cards.

Cards are derived from screener candidates and structured system fields only.
They do not create signals, do not place orders, and do not infer new claims.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from sentinel.core.config import OperatorProfile, load_config
from sentinel.core.types import utc_now

CardType = Literal["executable", "analysis_only"]

ANALYSIS_ONLY_BANNER = (
    "ANALYSIS ONLY - Sentinel does not route this instrument. "
    "Execute only via your external broker after independent review."
)


@dataclass(frozen=True)
class TradeResearchCard:
    card_id: str
    symbol: str
    screener: str
    card_type: CardType
    direction: str
    conviction_score: float
    entry_low: float
    entry_high: float
    stop_loss: float
    target_1: float
    target_2: float | None
    risk_reward: float
    suggested_quantity: int
    gross_position_value_inr: float
    risk_amount_inr: float
    estimated_round_trip_cost_inr: float
    estimated_tax_note: str
    thesis: str
    risks: list[str]
    feature_breakdown: dict[str, Any]
    warnings: list[str]
    amber_banner: str
    generated_at: str

    @property
    def is_executable(self) -> bool:
        return self.card_type == "executable"


class TradeResearchCardBuilder:
    """Build deterministic, auditable cards from screener candidates."""

    def __init__(self, profile: OperatorProfile | None = None) -> None:
        self.profile = profile or load_config()

    def build(self, candidate: dict[str, Any]) -> TradeResearchCard:
        symbol = str(candidate.get("symbol", "UNKNOWN"))
        entry_low = _num(candidate.get("entry_low"))
        entry_high = _num(candidate.get("entry_high"))
        stop_loss = _num(candidate.get("stop_loss"))
        target_1 = _num(candidate.get("target_1"))
        target_2 = candidate.get("target_2")
        qty = int(candidate.get("suggested_qty") or 1)
        entry_mid = (entry_low + entry_high) / 2
        risk_per_unit = abs(entry_mid - stop_loss)
        gross_value = entry_mid * qty
        risk_amount = risk_per_unit * qty
        card_type: CardType = (
            "analysis_only"
            if candidate.get("amber_banner") or candidate.get("execution_eligible") is False
            else "executable"
        )
        warnings = list(candidate.get("warnings", []))
        if risk_amount > float(self.profile.max_risk_per_trade_inr):
            warnings.append("Risk amount exceeds configured max risk per trade")
        if (candidate.get("rr_ratio") or 0) < self.profile.risk.min_risk_reward_ratio:
            warnings.append("Risk/reward is below configured minimum")
        if card_type == "analysis_only":
            warnings.append("Instrument is analysis-only inside Sentinel")

        return TradeResearchCard(
            card_id=f"{candidate.get('screener', 'unknown')}:{symbol}:{utc_now().date().isoformat()}",
            symbol=symbol,
            screener=str(candidate.get("screener", "unknown")),
            card_type=card_type,
            direction=str(candidate.get("direction", "BUY")),
            conviction_score=_num(candidate.get("conviction_score")),
            entry_low=round(entry_low, 4),
            entry_high=round(entry_high, 4),
            stop_loss=round(stop_loss, 4),
            target_1=round(target_1, 4),
            target_2=round(_num(target_2), 4) if target_2 is not None else None,
            risk_reward=round(_num(candidate.get("rr_ratio")), 2),
            suggested_quantity=qty,
            gross_position_value_inr=round(gross_value, 2),
            risk_amount_inr=round(risk_amount, 2),
            estimated_round_trip_cost_inr=round(_estimate_cost(gross_value, card_type), 2),
            estimated_tax_note=_tax_note(candidate),
            thesis=str(candidate.get("thesis", "")),
            risks=list(candidate.get("risks", [])),
            feature_breakdown=_feature_breakdown(candidate),
            warnings=warnings,
            amber_banner=ANALYSIS_ONLY_BANNER if card_type == "analysis_only" else "",
            generated_at=utc_now().isoformat(),
        )


def _num(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _estimate_cost(gross_value: float, card_type: CardType) -> float:
    if card_type == "analysis_only":
        return 0.0
    brokerage_floor = 20.0
    taxes_and_slippage_bps = 18.0
    return brokerage_floor + gross_value * taxes_and_slippage_bps / 10_000


def _tax_note(candidate: dict[str, Any]) -> str:
    if candidate.get("amber_banner"):
        return "Tax treatment depends on external broker/instrument jurisdiction."
    return "Indian cash equity: STCG/LTCG treatment depends on holding period."


def _feature_breakdown(candidate: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "sector",
        "cot_index",
        "cot_classification",
        "pips_to_stop",
        "is_inr_pair",
        "session_note",
        "quality_score",
        "valuation_score",
    ]
    return {key: candidate[key] for key in keys if key in candidate}
