"""
Covered-call hedge planner for Sprint 7.

Only hedging proposals are allowed. A candidate is valid only when the operator
already owns enough underlying shares to cover every option lot. Naked calls,
weekly directional options, and oversized F&O exposure are blocked in data, not
left as UI warnings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from sentinel.fo.greeks_dashboard import GreeksSnapshot, OptionContract, calculate_greeks

MAX_FO_EXPOSURE_PCT = 30.0
SPRINT7_LEARNING_EXPOSURE_WARNING_PCT = 20.0
MIN_DAYS_TO_EXPIRY = 8


@dataclass(frozen=True)
class EquityHolding:
    symbol: str
    quantity: int
    average_price: float
    last_price: float

    @property
    def market_value(self) -> float:
        return self.quantity * self.last_price


@dataclass(frozen=True)
class CoveredCallCandidate:
    holding: EquityHolding
    contract: OptionContract
    lots: int
    premium_income: float
    notional_exposure: float
    notional_exposure_pct: float
    max_covered_quantity: int
    greeks_snapshot: GreeksSnapshot
    warnings: list[str]

    @property
    def is_hedging_only(self) -> bool:
        return (
            self.contract.option_type == "call"
            and self.lots > 0
            and self.holding.quantity >= self.lots * self.contract.lot_size
        )


@dataclass(frozen=True)
class HedgeRejection:
    symbol: str
    reason: str


class CoveredCallPlanner:
    """Generate covered-call candidates from existing equity holdings."""

    def __init__(self, portfolio_value: float) -> None:
        if portfolio_value <= 0:
            raise ValueError("portfolio_value must be positive")
        self.portfolio_value = portfolio_value

    def evaluate(
        self,
        holding: EquityHolding,
        contract: OptionContract,
        requested_lots: int = 1,
    ) -> CoveredCallCandidate | HedgeRejection:
        if contract.underlying != holding.symbol:
            return HedgeRejection(contract.symbol, "Contract underlying does not match holding")
        if contract.option_type != "call":
            return HedgeRejection(contract.symbol, "Covered-call planner only accepts call options")
        if contract.is_weekly or contract.expiry_days < MIN_DAYS_TO_EXPIRY:
            return HedgeRejection(contract.symbol, "Weekly or expiry-day F&O entries are blocked")
        if requested_lots <= 0:
            return HedgeRejection(contract.symbol, "requested_lots must be positive")

        max_lots = holding.quantity // contract.lot_size
        if max_lots <= 0:
            return HedgeRejection(contract.symbol, "Insufficient held equity to cover one option lot")

        lots = min(requested_lots, max_lots)
        covered_qty = lots * contract.lot_size
        exposure = covered_qty * holding.last_price
        exposure_pct = exposure / self.portfolio_value * 100
        if exposure_pct > MAX_FO_EXPOSURE_PCT:
            return HedgeRejection(
                contract.symbol,
                f"F&O exposure {exposure_pct:.1f}% exceeds {MAX_FO_EXPOSURE_PCT:.0f}% cap",
            )

        warnings: list[str] = []
        if exposure_pct > SPRINT7_LEARNING_EXPOSURE_WARNING_PCT:
            warnings.append(
                "F&O exposure exceeds Sprint 7 learning threshold; operator acknowledgement required"
            )
        if contract.strike < holding.last_price:
            warnings.append("Strike is in-the-money; upside is already capped")

        return CoveredCallCandidate(
            holding=holding,
            contract=contract,
            lots=lots,
            premium_income=round(contract.last_price * covered_qty, 2),
            notional_exposure=round(exposure, 2),
            notional_exposure_pct=round(exposure_pct, 2),
            max_covered_quantity=max_lots * contract.lot_size,
            greeks_snapshot=calculate_greeks(contract, holding.last_price),
            warnings=warnings,
        )

    def generate_candidates(
        self,
        holdings: Sequence[EquityHolding],
        contracts: Sequence[OptionContract],
    ) -> list[CoveredCallCandidate]:
        candidates: list[CoveredCallCandidate] = []
        by_symbol = {holding.symbol: holding for holding in holdings}
        for contract in contracts:
            holding = by_symbol.get(contract.underlying)
            if holding is None:
                continue
            result = self.evaluate(holding, contract)
            if isinstance(result, CoveredCallCandidate):
                candidates.append(result)
        return sorted(candidates, key=lambda c: c.premium_income, reverse=True)
