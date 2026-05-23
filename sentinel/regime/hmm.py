"""
Sprint 8 market regime posterior classifier.

Uses a compact fixed-parameter Gaussian HMM-style forward pass. It is designed
for operator context and sizing overlays, not for trade signal generation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Sequence


class RegimeState(str, Enum):
    CALM_UPTREND = "calm_uptrend"
    CHOPPY_NEUTRAL = "choppy_neutral"
    STRESS = "stress"


@dataclass(frozen=True)
class RegimePosterior:
    state: RegimeState
    probabilities: dict[RegimeState, float]
    vix_defensive: bool
    confidence: float
    recommended_risk_multiplier: float
    rationale: str


STATE_PARAMS = {
    RegimeState.CALM_UPTREND: {"mu": 0.0008, "sigma": 0.007},
    RegimeState.CHOPPY_NEUTRAL: {"mu": 0.0, "sigma": 0.012},
    RegimeState.STRESS: {"mu": -0.0012, "sigma": 0.022},
}

TRANSITION = {
    RegimeState.CALM_UPTREND: {
        RegimeState.CALM_UPTREND: 0.88,
        RegimeState.CHOPPY_NEUTRAL: 0.10,
        RegimeState.STRESS: 0.02,
    },
    RegimeState.CHOPPY_NEUTRAL: {
        RegimeState.CALM_UPTREND: 0.16,
        RegimeState.CHOPPY_NEUTRAL: 0.74,
        RegimeState.STRESS: 0.10,
    },
    RegimeState.STRESS: {
        RegimeState.CALM_UPTREND: 0.04,
        RegimeState.CHOPPY_NEUTRAL: 0.20,
        RegimeState.STRESS: 0.76,
    },
}


def classify_regime(
    daily_returns: Sequence[float],
    india_vix: float,
    dxy_20d_change_pct: float = 0.0,
) -> RegimePosterior:
    """Classify market regime from daily returns, India VIX, and DXY context."""
    if not daily_returns:
        return _fallback(india_vix, "Insufficient return history")

    states = list(RegimeState)
    posterior = {state: 1.0 / len(states) for state in states}
    for ret in daily_returns[-90:]:
        predicted = {
            to_state: sum(posterior[from_state] * TRANSITION[from_state][to_state]
                          for from_state in states)
            for to_state in states
        }
        likelihood = {
            state: _gaussian_pdf(ret, STATE_PARAMS[state]["mu"], STATE_PARAMS[state]["sigma"])
            for state in states
        }
        posterior = _normalize({
            state: predicted[state] * likelihood[state]
            for state in states
        })

    posterior = _apply_macro_overlay(posterior, india_vix, dxy_20d_change_pct)
    state = max(posterior, key=posterior.get)
    confidence = posterior[state]
    return RegimePosterior(
        state=state,
        probabilities={s: round(posterior[s], 4) for s in states},
        vix_defensive=india_vix > 22,
        confidence=round(confidence, 4),
        recommended_risk_multiplier=_risk_multiplier(state, india_vix),
        rationale=_rationale(state, india_vix, dxy_20d_change_pct),
    )


def _fallback(india_vix: float, rationale: str) -> RegimePosterior:
    state = RegimeState.STRESS if india_vix > 22 else RegimeState.CHOPPY_NEUTRAL
    probs = {
        RegimeState.CALM_UPTREND: 0.2,
        RegimeState.CHOPPY_NEUTRAL: 0.5 if state == RegimeState.CHOPPY_NEUTRAL else 0.2,
        RegimeState.STRESS: 0.6 if state == RegimeState.STRESS else 0.3,
    }
    return RegimePosterior(
        state=state,
        probabilities=probs,
        vix_defensive=india_vix > 22,
        confidence=probs[state],
        recommended_risk_multiplier=_risk_multiplier(state, india_vix),
        rationale=rationale,
    )


def _apply_macro_overlay(
    posterior: dict[RegimeState, float],
    india_vix: float,
    dxy_20d_change_pct: float,
) -> dict[RegimeState, float]:
    adjusted = dict(posterior)
    if india_vix > 22:
        adjusted[RegimeState.STRESS] *= 2.0
        adjusted[RegimeState.CALM_UPTREND] *= 0.65
        adjusted = _normalize(adjusted)
        if adjusted[RegimeState.STRESS] < 0.35:
            deficit = 0.35 - adjusted[RegimeState.STRESS]
            adjusted[RegimeState.STRESS] = 0.35
            adjusted[RegimeState.CALM_UPTREND] = max(
                0.0,
                adjusted[RegimeState.CALM_UPTREND] - deficit * 0.6,
            )
            adjusted[RegimeState.CHOPPY_NEUTRAL] = max(
                0.0,
                adjusted[RegimeState.CHOPPY_NEUTRAL] - deficit * 0.4,
            )
    if dxy_20d_change_pct > 2.0:
        adjusted[RegimeState.STRESS] *= 1.25
        adjusted[RegimeState.CALM_UPTREND] *= 0.85
    elif dxy_20d_change_pct < -2.0:
        adjusted[RegimeState.CALM_UPTREND] *= 1.15
    return _normalize(adjusted)


def _risk_multiplier(state: RegimeState, india_vix: float) -> float:
    if state == RegimeState.STRESS or india_vix > 22:
        return 0.5
    if state == RegimeState.CHOPPY_NEUTRAL:
        return 0.75
    return 1.0


def _rationale(state: RegimeState, india_vix: float, dxy_20d_change_pct: float) -> str:
    parts = [f"{state.value.replace('_', ' ')} has highest posterior"]
    if india_vix > 22:
        parts.append("India VIX defensive threshold is active")
    if abs(dxy_20d_change_pct) > 2:
        parts.append(f"DXY 20d move is {dxy_20d_change_pct:+.1f}%")
    return "; ".join(parts)


def _gaussian_pdf(value: float, mu: float, sigma: float) -> float:
    sigma = max(sigma, 1e-9)
    z = (value - mu) / sigma
    return math.exp(-0.5 * z * z) / (sigma * math.sqrt(2 * math.pi))


def _normalize(values: dict[RegimeState, float]) -> dict[RegimeState, float]:
    total = sum(values.values())
    if total <= 0:
        return {state: 1.0 / len(values) for state in values}
    return {state: value / total for state, value in values.items()}
