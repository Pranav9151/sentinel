"""
Option pricing and Greeks for Sprint 7 hedging-only F&O.

This module is deterministic and broker-independent. It exists so every
candidate option hedge has Delta/Gamma/Theta/Vega/Rho visible before review.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

OptionType = Literal["call", "put"]


@dataclass(frozen=True)
class OptionContract:
    symbol: str
    underlying: str
    option_type: OptionType
    strike: float
    expiry_days: int
    lot_size: int
    last_price: float
    implied_vol_pct: float
    risk_free_rate_pct: float = 6.5

    @property
    def is_weekly(self) -> bool:
        return self.expiry_days <= 7


@dataclass(frozen=True)
class Greeks:
    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float


@dataclass(frozen=True)
class GreeksSnapshot:
    contract: OptionContract
    underlying_price: float
    theoretical_price: float
    greeks: Greeks
    intrinsic_value: float
    time_value: float


def calculate_greeks(contract: OptionContract, underlying_price: float) -> GreeksSnapshot:
    """Return Black-Scholes price and Greeks for a European option approximation."""
    if underlying_price <= 0:
        raise ValueError("underlying_price must be positive")
    if contract.strike <= 0:
        raise ValueError("strike must be positive")
    if contract.expiry_days <= 0:
        raise ValueError("expiry_days must be positive")
    if contract.implied_vol_pct <= 0:
        raise ValueError("implied_vol_pct must be positive")

    time_to_expiry = contract.expiry_days / 365.0
    volatility = contract.implied_vol_pct / 100.0
    risk_free = contract.risk_free_rate_pct / 100.0
    sqrt_t = math.sqrt(time_to_expiry)
    d1 = (
        math.log(underlying_price / contract.strike)
        + (risk_free + 0.5 * volatility**2) * time_to_expiry
    ) / (volatility * sqrt_t)
    d2 = d1 - volatility * sqrt_t

    if contract.option_type == "call":
        theoretical = (
            underlying_price * _norm_cdf(d1)
            - contract.strike * math.exp(-risk_free * time_to_expiry) * _norm_cdf(d2)
        )
        delta = _norm_cdf(d1)
        theta = (
            -underlying_price * _norm_pdf(d1) * volatility / (2 * sqrt_t)
            - risk_free * contract.strike * math.exp(-risk_free * time_to_expiry) * _norm_cdf(d2)
        ) / 365.0
        rho = (
            contract.strike
            * time_to_expiry
            * math.exp(-risk_free * time_to_expiry)
            * _norm_cdf(d2)
        ) / 100.0
        intrinsic = max(0.0, underlying_price - contract.strike)
    else:
        theoretical = (
            contract.strike * math.exp(-risk_free * time_to_expiry) * _norm_cdf(-d2)
            - underlying_price * _norm_cdf(-d1)
        )
        delta = _norm_cdf(d1) - 1
        theta = (
            -underlying_price * _norm_pdf(d1) * volatility / (2 * sqrt_t)
            + risk_free * contract.strike * math.exp(-risk_free * time_to_expiry) * _norm_cdf(-d2)
        ) / 365.0
        rho = (
            -contract.strike
            * time_to_expiry
            * math.exp(-risk_free * time_to_expiry)
            * _norm_cdf(-d2)
        ) / 100.0
        intrinsic = max(0.0, contract.strike - underlying_price)

    gamma = _norm_pdf(d1) / (underlying_price * volatility * sqrt_t)
    vega = underlying_price * _norm_pdf(d1) * sqrt_t / 100.0
    time_value = max(0.0, contract.last_price - intrinsic)

    return GreeksSnapshot(
        contract=contract,
        underlying_price=round(underlying_price, 2),
        theoretical_price=round(theoretical, 2),
        greeks=Greeks(
            delta=round(delta, 4),
            gamma=round(gamma, 6),
            theta=round(theta, 4),
            vega=round(vega, 4),
            rho=round(rho, 4),
        ),
        intrinsic_value=round(intrinsic, 2),
        time_value=round(time_value, 2),
    )


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
