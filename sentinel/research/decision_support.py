"""
Structured investment and trading decision-support reports.

This module produces deterministic research-only reports. It does not create
orders, does not guarantee outcomes, and does not override operator judgment.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from sentinel.core.config import OperatorProfile, load_config
from sentinel.core.types import utc_now
from sentinel.data.forex_connector import ForexConnector, PAIR_MAP
from sentinel.data.fundamental_store import FundamentalStore
from sentinel.data.historical_store import HistoricalStore
from sentinel.data.market_data import MarketDataStore
from sentinel.data.mf_advisor import MOCK_FUNDS
from sentinel.data.mock_data import ALL_MOCK_STOCKS
from sentinel.indicators.technical import compute_all

AssetType = Literal["equity", "forex", "mutual_fund"]


@dataclass(frozen=True)
class ResearchRequest:
    asset_type: AssetType
    symbol: str
    horizon: str = "swing"
    capital_inr: float | None = None


def build_research_report(
    request: ResearchRequest,
    profile: OperatorProfile | None = None,
) -> dict[str, Any]:
    """Build an A-J research report for one supported asset."""
    profile = profile or load_config()
    symbol = request.symbol.upper().strip()
    if request.asset_type == "equity":
        return _equity_report(symbol, request, profile)
    if request.asset_type == "forex":
        return _forex_report(symbol, request, profile)
    if request.asset_type == "mutual_fund":
        return _mutual_fund_report(symbol, request, profile)
    raise ValueError(f"Unsupported asset_type: {request.asset_type}")


def supported_assets() -> dict[str, list[dict[str, str]]]:
    return {
        "equities": [
            {"symbol": symbol, "name": str(info["name"]), "sector": str(info["sector"])}
            for symbol, info in sorted(ALL_MOCK_STOCKS.items())
        ],
        "forex": [
            {"symbol": symbol, "name": data["twelve"], "sector": "FX/Commodity"}
            for symbol, data in sorted(PAIR_MAP.items())
        ],
        "mutual_funds": [
            {"symbol": key, "name": str(fund["name"]), "sector": str(fund["category"])}
            for key, fund in sorted(MOCK_FUNDS.items())
        ],
    }


def _equity_report(
    symbol: str,
    request: ResearchRequest,
    profile: OperatorProfile,
) -> dict[str, Any]:
    if symbol not in ALL_MOCK_STOCKS:
        raise ValueError(f"{symbol} is not in the supported NSE equity universe.")

    stock = ALL_MOCK_STOCKS[symbol]
    store = HistoricalStore()
    fund_store = FundamentalStore()
    market = MarketDataStore()
    fund_store.ingest(symbol)
    fundamentals = fund_store.compute_quality_score(symbol)
    raw_fundamentals = fund_store.get_latest(symbol) or {}
    bars = store.get_ohlcv(symbol, as_of=utc_now(), lookback_days=260)
    if len(bars) < 60:
        bars = store.kite.get_historical(symbol, days=260)
    indicators = compute_all(bars)
    latest = float(bars[-1].close) if bars else float(stock["price"])
    atr = _safe_float(indicators.get("atr_14"), latest * 0.02)
    support = _safe_float(indicators.get("s1"), latest - 2 * atr)
    resistance = _safe_float(indicators.get("r1"), latest + 2 * atr)
    stop = round(max(support, latest - 1.8 * atr), 2)
    target_1 = round(latest + 2.2 * atr, 2)
    target_2 = round(latest + 3.5 * atr, 2)
    risk_per_share = max(latest - stop, 0.01)
    rr = round((target_1 - latest) / risk_per_share, 2)
    qty = _position_size(profile, latest, stop, request.capital_inr)
    trend = _trend_from_indicators(indicators)
    risk_level = _risk_level(atr / max(latest, 0.01) * 100, rr)
    action = _action_category(
        quality_score=_safe_float(fundamentals.get("quality_score"), 0),
        trend=trend,
        rr=rr,
        risk_level=risk_level,
    )
    market_context = _market_context(market)
    data_warnings = _base_data_warnings()
    if not raw_fundamentals:
        data_warnings.append("Fundamental snapshot unavailable for this symbol.")

    return _ordered_report({
        "A. Executive Summary": {
            "final_view": action,
            "opportunity_type": _opportunity_type(request.horizon),
            "time_horizon": request.horizon,
            "confidence_score": _confidence_score(fundamentals, trend, rr),
            "risk_level": risk_level,
        },
        "B. Market Context": {
            "overall_market_condition": market_context,
            "sector_condition": f"{stock['sector']} sector context should be confirmed against live breadth before entry.",
            "macro_impact": "USD/INR, crude, rates, FII/DII and VIX can affect position sizing.",
        },
        "C. Asset Overview": {
            "name": stock["name"],
            "symbol": symbol,
            "category": "NSE equity",
            "current_price_or_latest_available": round(latest, 2),
            "data_timestamp": bars[-1].timestamp.isoformat() if bars else utc_now().isoformat(),
        },
        "D. Fundamental View": {
            "key_strengths": _fundamental_strengths(fundamentals, raw_fundamentals),
            "key_weaknesses": _fundamental_weaknesses(fundamentals, raw_fundamentals),
            "valuation_view": _valuation_view(fundamentals),
            "financial_health": _financial_health(raw_fundamentals),
            "institutional_stakeholder_view": _stakeholder_view(raw_fundamentals),
        },
        "E. Technical View": {
            "current_trend": trend,
            "important_levels": {
                "support": round(support, 2),
                "resistance": round(resistance, 2),
                "stop_reference": stop,
            },
            "indicators": _technical_snapshot(indicators),
            "volume_analysis": _volume_view(indicators),
            "multi_timeframe_view": _multi_timeframe_view(indicators),
        },
        "F. News and Sentiment View": {
            "positive_news": [],
            "negative_news": [],
            "neutral_developments": ["Live news connector is not configured in this local build."],
            "sentiment_impact": "Treat news sentiment as missing until a live news API is connected.",
        },
        "G. Trade or Investment Plan": {
            "entry_zone": [round(latest * 0.995, 2), round(latest * 1.005, 2)],
            "safer_entry_level": round(max(latest, resistance), 2),
            "aggressive_entry": round(latest, 2),
            "confirmation_entry": round(max(latest, resistance), 2),
            "stop_loss": stop,
            "target_1": target_1,
            "target_2": target_2,
            "target_3": round(latest + 5.0 * atr, 2),
            "trailing_stop_loss_method": "Trail below higher swing lows or 1.5x ATR after Target 1.",
            "exit_rule": "Exit if stop-loss closes below or the thesis invalidates.",
            "re_entry_rule": "Re-enter only after price regains the confirmation level with volume.",
            "avoid_entry_condition": "Avoid if price gaps far above resistance or R:R falls below 1:2.",
            "partial_profit_booking_strategy": "Book partial profit near Target 1 and trail remaining quantity.",
            "capital_protection_rules": "No averaging down without a fresh setup; never exceed configured risk capital.",
            "risk_reward_ratio": rr,
            "suggested_quantity": qty,
            "max_loss_estimate_inr": round(qty * risk_per_share, 2),
        },
        "H. Scenario Analysis": {
            "bullish_case": f"Price holds above support and breaks {round(resistance, 2)} with volume confirmation.",
            "bearish_case": f"Price loses {stop} or market breadth/VIX deteriorates.",
            "sideways_case": "Price remains between support and resistance; wait for confirmation.",
            "best_case_outcome": f"Trend extension toward {round(latest + 5.0 * atr, 2)} with controlled pullbacks.",
            "worst_case_outcome": f"Stop-loss at {stop} is hit; estimated loss stays within planned risk.",
            "most_likely_outcome": "Follow the action category; wait if confirmation and risk-reward are weak.",
            "what_could_go_wrong": "False breakout, broad-market reversal, stale data, or sudden adverse news.",
            "confirmation_signal": "Close above confirmation level with volume and stable market breadth.",
            "thesis_invalidation_level": stop,
        },
        "I. Final Decision": {
            "action_category": action,
            "confidence_score_out_of_10": _confidence_score(fundamentals, trend, rr),
            "risk_score_out_of_10": _risk_score(risk_level),
            "suitability": _suitability(request.horizon, rr, risk_level),
            "final_explanation": _plain_final(action, rr, risk_level),
        },
        "J. Data Quality Warning": {
            "missing_data": data_warnings,
            "stale_data": ["Local mock data may not reflect the latest market session."],
            "assumptions": ["Report is research-only and uses configured operator risk limits."],
            "live_market_confirmation_required": True,
        },
    }, request)


def _forex_report(
    symbol: str,
    request: ResearchRequest,
    profile: OperatorProfile,
) -> dict[str, Any]:
    if symbol not in PAIR_MAP:
        raise ValueError(f"{symbol} is not in the supported forex universe.")
    connector = ForexConnector()
    bars = connector.get_forex_ohlcv(symbol, periods=220)
    indicators = compute_all(bars)
    rate = connector.get_live_forex_rate(symbol)
    latest = _safe_float(rate.get("mid"), float(bars[-1].close) if bars else 0)
    atr = _safe_float(indicators.get("atr_14"), latest * 0.006)
    stop = round(latest - 1.6 * atr, 5)
    target_1 = round(latest + 2.4 * atr, 5)
    rr = round((target_1 - latest) / max(latest - stop, 0.00001), 2)
    trend = _trend_from_indicators(indicators)
    risk_level = _risk_level(atr / max(latest, 0.00001) * 100, rr)
    action = "Buy Only Above Confirmation Level" if rr >= 2 else "Wait for Better Entry"
    overlay = connector.get_macro_overlay()
    data_warnings = _base_data_warnings()
    if rate.get("amber_banner"):
        data_warnings.append("This is analysis-only in Sentinel and is not routed for execution.")

    return _ordered_report({
        "A. Executive Summary": {
            "final_view": action,
            "opportunity_type": "Forex research setup",
            "time_horizon": request.horizon,
            "confidence_score": 6 if rr >= 2 else 4,
            "risk_level": risk_level,
        },
        "B. Market Context": {
            "overall_market_condition": "FX setups are sensitive to DXY, yields, central-bank policy and event risk.",
            "sector_condition": "Currency pair, not equity sector.",
            "macro_impact": {
                "dxy_regime": getattr(overlay.dxy_regime, "value", None),
                "us_10y_yield": overlay.us_10y_yield,
                "usd_inr": overlay.usd_inr,
            },
        },
        "C. Asset Overview": {
            "name": PAIR_MAP[symbol]["twelve"],
            "symbol": symbol,
            "category": "Forex/commodity pair",
            "current_price_or_latest_available": latest,
            "data_timestamp": rate.get("timestamp", utc_now()).isoformat() if hasattr(rate.get("timestamp"), "isoformat") else utc_now().isoformat(),
        },
        "D. Fundamental View": {
            "key_strengths": ["Macro direction can support trend continuation if rates and DXY align."],
            "key_weaknesses": ["Central-bank or inflation surprises can invalidate technical setups quickly."],
            "valuation_view": "Traditional equity valuation ratios do not apply to forex.",
            "financial_health": "Use macro health: inflation, GDP, employment and rates.",
            "institutional_stakeholder_view": "COT and positioning should be confirmed before sizing aggressively.",
        },
        "E. Technical View": {
            "current_trend": trend,
            "important_levels": {
                "support": round(_safe_float(indicators.get("s1"), latest - 2 * atr), 5),
                "resistance": round(_safe_float(indicators.get("r1"), latest + 2 * atr), 5),
                "stop_reference": stop,
            },
            "indicators": _technical_snapshot(indicators),
            "volume_analysis": "Spot FX volume is proxy-based; do not rely on it like exchange equity volume.",
            "multi_timeframe_view": _multi_timeframe_view(indicators),
        },
        "F. News and Sentiment View": {
            "positive_news": [],
            "negative_news": [],
            "neutral_developments": ["Economic calendar should be checked before any entry."],
            "sentiment_impact": "News can dominate FX; avoid entries near high-impact events.",
        },
        "G. Trade or Investment Plan": {
            "entry_zone": [round(latest * 0.999, 5), round(latest * 1.001, 5)],
            "safer_entry_level": round(_safe_float(indicators.get("r1"), latest + atr), 5),
            "aggressive_entry": round(latest, 5),
            "confirmation_entry": round(_safe_float(indicators.get("r1"), latest + atr), 5),
            "stop_loss": stop,
            "target_1": target_1,
            "target_2": round(latest + 3.8 * atr, 5),
            "target_3": None,
            "trailing_stop_loss_method": "Trail with recent swing levels after event risk clears.",
            "exit_rule": "Exit if price closes beyond stop or a high-impact event changes the thesis.",
            "re_entry_rule": "Re-enter only after post-event volatility normalizes and trend reconfirms.",
            "avoid_entry_condition": "Avoid near high-impact calendar events or when spreads widen sharply.",
            "partial_profit_booking_strategy": "Scale out near Target 1; do not add leverage after a loss.",
            "capital_protection_rules": "No over-leverage; keep this as analysis-only unless broker sizing is verified.",
            "risk_reward_ratio": rr,
            "suggested_quantity": 0,
            "max_loss_estimate_inr": "External broker sizing required.",
        },
        "H. Scenario Analysis": {
            "bullish_case": "Trend continues with supportive DXY/rate differential.",
            "bearish_case": "Macro surprise reverses the pair and breaks support.",
            "sideways_case": "Pair chops around event risk; wait for London/New York confirmation.",
            "best_case_outcome": "Clean trend continuation after the next macro event confirms direction.",
            "worst_case_outcome": "Stop is hit quickly due to a policy or inflation surprise.",
            "most_likely_outcome": "Wait for event-risk clarity unless confirmation and spread quality are present.",
            "what_could_go_wrong": "Central-bank surprise, liquidity gap, news shock, or bad broker execution.",
            "confirmation_signal": "Break of resistance with stable spreads and supportive macro overlay.",
            "thesis_invalidation_level": stop,
        },
        "I. Final Decision": {
            "action_category": action,
            "confidence_score_out_of_10": 6 if rr >= 2 else 4,
            "risk_score_out_of_10": _risk_score(risk_level),
            "suitability": "forex analysis-only",
            "final_explanation": _plain_final(action, rr, risk_level),
        },
        "J. Data Quality Warning": {
            "missing_data": data_warnings,
            "stale_data": ["Live macro/news/event confirmation is required."],
            "assumptions": ["No Sentinel execution routing for global analysis-only instruments."],
            "live_market_confirmation_required": True,
        },
    }, request)


def _mutual_fund_report(
    symbol: str,
    request: ResearchRequest,
    profile: OperatorProfile,
) -> dict[str, Any]:
    if symbol not in MOCK_FUNDS:
        raise ValueError(f"{symbol} is not in the supported mutual fund universe.")
    fund = MOCK_FUNDS[symbol]
    risk = str(fund["risk"])
    confidence = 8 if symbol in {"PPFAS_FLEXI", "MIRAE_LARGECAP", "ICICI_BALANCED"} else 6
    action = (
        "Long-Term Investment Candidate"
        if risk in {"conservative", "moderate"}
        else "High Risk / Speculative"
    )
    return _ordered_report({
        "A. Executive Summary": {
            "final_view": action,
            "opportunity_type": "Mutual fund research",
            "time_horizon": "long-term SIP",
            "confidence_score": confidence,
            "risk_level": risk.replace("_", " ").title(),
        },
        "B. Market Context": {
            "overall_market_condition": "SIP suitability depends on valuation, volatility, horizon and existing allocation.",
            "sector_condition": f"{fund['category']} category.",
            "macro_impact": "Equity funds are sensitive to rates, earnings growth, liquidity and risk appetite.",
        },
        "C. Asset Overview": {
            "name": fund["name"],
            "symbol": symbol,
            "category": fund["category"],
            "current_price_or_latest_available": "NAV not connected in local mock mode.",
            "data_timestamp": utc_now().isoformat(),
        },
        "D. Fundamental View": {
            "key_strengths": [
                f"5Y return {fund['returns_5y']}%",
                f"Alpha 5Y {fund['alpha_5y']}%",
                f"Manager tenure {fund['manager_yrs']} years",
            ],
            "key_weaknesses": _fund_risks(fund),
            "valuation_view": f"Portfolio PE around {fund['pe_portfolio']}x in mock data.",
            "financial_health": f"AUM {fund['aum_cr']} Cr, expense ratio {fund['expense']}%.",
            "institutional_stakeholder_view": "Use AMFI portfolio disclosures for live holding changes.",
        },
        "E. Technical View": {
            "current_trend": "Mutual funds should not be judged by short-term chart signals alone.",
            "important_levels": "Use index/category drawdowns, not intraday levels.",
            "indicators": {
                "standard_deviation": fund["std_dev"],
                "sharpe": fund["sharpe"],
                "sortino": fund["sortino"],
            },
            "volume_analysis": "Not applicable.",
            "multi_timeframe_view": {
                "1y": fund["returns_1y"],
                "3y": fund["returns_3y"],
                "5y": fund["returns_5y"],
                "10y": fund["returns_10y"],
            },
        },
        "F. News and Sentiment View": {
            "positive_news": [],
            "negative_news": [],
            "neutral_developments": ["Fund manager and portfolio changes require live AMFI/factsheet confirmation."],
            "sentiment_impact": "Secondary only; use process, risk and overlap first.",
        },
        "G. Trade or Investment Plan": {
            "entry_zone": "Prefer SIP/STP over one-shot timing unless valuation is deeply attractive.",
            "safer_entry_level": "Use monthly SIP; add only on broad market corrections if allocation permits.",
            "stop_loss": "Not applicable like trading; exit on thesis/fund-quality deterioration.",
            "target_1": "Goal-based corpus target.",
            "target_2": None,
            "target_3": None,
            "exit_rule": "Exit/reduce if manager/process changes, persistent underperformance or goal nears.",
            "re_entry_rule": "Re-enter after category/fund quality recovers and portfolio overlap is acceptable.",
            "avoid_entry_condition": "Avoid lump sum if horizon is short, overlap is high, or risk profile mismatches.",
            "partial_profit_booking_strategy": "Shift gradually to lower-risk funds as the financial goal approaches.",
            "capital_protection_rules": "Keep emergency fund separate; do not use SIP money needed within three years.",
            "risk_reward_ratio": "Not applicable to MF SIP.",
        },
        "H. Scenario Analysis": {
            "bullish_case": "Category outperforms and fund maintains alpha with controlled downside.",
            "bearish_case": "Category derates or fund underperforms benchmark for multiple review periods.",
            "sideways_case": "Continue SIP if goal horizon is long and fund quality remains intact.",
            "best_case_outcome": "Fund compounds with persistent alpha and tolerable drawdowns.",
            "worst_case_outcome": "Category drawdown plus fund underperformance requires switching or pausing allocation.",
            "most_likely_outcome": "Use SIP discipline and review category/fund quality periodically.",
            "what_could_go_wrong": "Manager change, style drift, high portfolio overlap, or tax/rule changes.",
            "confirmation_signal": "Recent factsheet confirms holdings, expense, risk metrics and manager process.",
            "thesis_invalidation_level": "Process drift, manager instability, high overlap or sustained underperformance.",
        },
        "I. Final Decision": {
            "action_category": action,
            "confidence_score_out_of_10": confidence,
            "risk_score_out_of_10": _fund_risk_score(risk),
            "suitability": "SIP / long-term" if risk != "very_high" else "avoid for conservative SIP",
            "final_explanation": "Use this as allocation research, not a guaranteed return expectation.",
        },
        "J. Data Quality Warning": {
            "missing_data": ["Live NAV, current portfolio holdings, overlap and latest factsheet are not connected."],
            "stale_data": ["Mock fund database may not match latest AMFI factsheets."],
            "assumptions": ["Tax treatment is high-level and depends on holding period and category rules."],
            "live_market_confirmation_required": True,
        },
    }, request)


def _ordered_report(sections: dict[str, Any], request: ResearchRequest) -> dict[str, Any]:
    return {
        "report_type": "structured_research_decision_support",
        "asset_type": request.asset_type,
        "symbol": request.symbol.upper().strip(),
        "generated_at": utc_now().isoformat(),
        "research_only": True,
        "operator_warning": (
            "This is research support, not a blind buy/sell tip. "
            "Final execution responsibility remains with the user."
        ),
        "sections": sections,
    }


def _market_context(market: MarketDataStore) -> dict[str, Any]:
    bias = market.get_market_bias()
    return {
        "bias": bias.get("bias"),
        "score": bias.get("score"),
        "vix": bias.get("vix"),
        "fii_trend": bias.get("fii_trend"),
    }


def _fundamental_strengths(quality: dict[str, Any], raw: dict[str, Any]) -> list[str]:
    strengths = []
    if _safe_float(raw.get("roe_pct"), 0) >= 15:
        strengths.append("ROE is above the 15% quality threshold.")
    if _safe_float(raw.get("debt_to_equity"), 99) <= 1:
        strengths.append("Debt/equity is within conservative limits.")
    if _safe_float(raw.get("promoter_pledging_pct"), 99) <= 5:
        strengths.append("Promoter pledging is low.")
    if _safe_float(quality.get("quality_score"), 0) >= 7:
        strengths.append("Composite quality score is strong.")
    return strengths or ["No clear fundamental strength found in available data."]


def _fundamental_weaknesses(quality: dict[str, Any], raw: dict[str, Any]) -> list[str]:
    weaknesses = []
    if _safe_float(raw.get("roe_pct"), 0) < 12:
        weaknesses.append("ROE is below preferred quality threshold.")
    if _safe_float(raw.get("debt_to_equity"), 0) > 1.5:
        weaknesses.append("Debt/equity is elevated.")
    if _safe_float(raw.get("promoter_pledging_pct"), 0) > 10:
        weaknesses.append("Promoter pledging is high.")
    if _safe_float(quality.get("valuation_score"), 0) <= 3:
        weaknesses.append("Valuation comfort is weak.")
    return weaknesses or ["No major weakness flagged by available local data."]


def _valuation_view(quality: dict[str, Any]) -> str:
    score = _safe_float(quality.get("valuation_score"), 0)
    if score >= 7:
        return "Valuation appears relatively comfortable versus available sector proxy."
    if score >= 4:
        return "Valuation is fair but not a bargain."
    return "Valuation comfort is weak; wait for better entry or stronger growth evidence."


def _financial_health(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "pe_ratio": raw.get("pe_ratio"),
        "sector_pe": raw.get("sector_pe"),
        "market_cap_cr": raw.get("market_cap_cr"),
        "roe_pct": raw.get("roe_pct"),
        "roce_pct": raw.get("roce_pct"),
        "debt_to_equity": raw.get("debt_to_equity"),
        "ebitda_margin_pct": raw.get("ebitda_margin_pct"),
        "net_profit_margin_pct": raw.get("net_profit_margin_pct"),
        "revenue_growth_yoy_pct": raw.get("revenue_growth_yoy_pct"),
        "missing_metrics_note": "EPS growth, cash flow, EV/EBITDA, PB and management commentary require live fundamentals integration.",
    }


def _stakeholder_view(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "promoter_holding_pct": raw.get("promoter_holding_pct"),
        "promoter_pledging_pct": raw.get("promoter_pledging_pct"),
        "fii_holding_pct": raw.get("fii_holding_pct"),
        "dii_holding_pct": raw.get("dii_holding_pct"),
        "note": "Bulk/block/insider activity requires live NSE filings integration.",
    }


def _technical_snapshot(indicators: dict[str, Any]) -> dict[str, Any]:
    return {
        "rsi_14": indicators.get("rsi_14"),
        "rsi_zone": indicators.get("rsi_zone"),
        "macd": indicators.get("macd_macd"),
        "macd_signal": indicators.get("macd_signal"),
        "atr_14": indicators.get("atr_14"),
        "atr_14_pct": indicators.get("atr_14_pct"),
        "bollinger_pct_b": indicators.get("bb_pct_b"),
    }


def _trend_from_indicators(indicators: dict[str, Any]) -> str:
    if indicators.get("ma_stack_bullish"):
        return "Uptrend with bullish moving-average stack"
    if indicators.get("ma_stack_bearish"):
        return "Downtrend with bearish moving-average stack"
    return str(indicators.get("trend_direction") or "Mixed / sideways")


def _volume_view(indicators: dict[str, Any]) -> str:
    ratio = _safe_float(indicators.get("vol_ratio"), 0)
    if ratio >= 2:
        return f"Volume confirmation is strong at {ratio:.2f}x average."
    if ratio >= 1.2:
        return f"Volume is mildly supportive at {ratio:.2f}x average."
    return "Volume confirmation is weak or unavailable."


def _multi_timeframe_view(indicators: dict[str, Any]) -> dict[str, Any]:
    return {
        "daily": indicators.get("trend_direction", "mixed"),
        "weekly": "Confirm manually with weekly chart before positional sizing.",
        "monthly": "Use monthly trend only for long-term allocation decisions.",
        "intraday": "Use intraday only for execution refinement, not thesis generation.",
    }


def _position_size(
    profile: OperatorProfile,
    entry: float,
    stop: float,
    capital_override: float | None,
) -> int:
    risk_capital = capital_override or float(profile.max_risk_per_trade_inr)
    risk_per_unit = max(abs(entry - stop), 0.01)
    return max(1, int(risk_capital / risk_per_unit))


def _risk_level(vol_pct: float, rr: float) -> str:
    if rr < 1.5 or vol_pct > 4:
        return "Very High"
    if rr < 2 or vol_pct > 2.5:
        return "High"
    if vol_pct > 1.2:
        return "Medium"
    return "Low"


def _risk_score(risk_level: str) -> int:
    return {"Low": 3, "Medium": 5, "High": 7, "Very High": 9}.get(risk_level, 6)


def _fund_risk_score(risk: str) -> int:
    return {"conservative": 3, "moderate": 5, "high": 7, "very_high": 9}.get(risk, 6)


def _confidence_score(quality: dict[str, Any], trend: str, rr: float) -> int:
    score = 4
    if _safe_float(quality.get("quality_score"), 0) >= 7:
        score += 2
    if "Uptrend" in trend:
        score += 1
    if rr >= 2:
        score += 1
    return max(1, min(10, score))


def _action_category(
    quality_score: float,
    trend: str,
    rr: float,
    risk_level: str,
) -> str:
    if rr < 2:
        return "Avoid"
    if risk_level in {"High", "Very High"}:
        return "High Risk / Speculative"
    if quality_score >= 7 and "Uptrend" in trend:
        return "Buy Only Above Confirmation Level"
    if quality_score >= 7:
        return "Accumulate on Dips"
    return "Wait for Better Entry"


def _suitability(horizon: str, rr: float, risk_level: str) -> str:
    if rr < 2 or risk_level == "Very High":
        return "avoid"
    if horizon in {"intraday", "swing", "positional", "long-term"}:
        return horizon
    return "swing"


def _opportunity_type(horizon: str) -> str:
    return {
        "intraday": "Short-Term Trade Candidate",
        "swing": "Swing trade research setup",
        "positional": "Positional trade research setup",
        "long-term": "Long-Term Investment Candidate",
    }.get(horizon, "Research watchlist candidate")


def _plain_final(action: str, rr: float, risk_level: str) -> str:
    if action == "Avoid":
        return "Risk-reward is not good enough. Protect capital and wait."
    return (
        f"{action}. Risk is {risk_level}; use confirmation and respect the stop. "
        f"Current planned risk-reward is about 1:{rr}."
    )


def _fund_risks(fund: dict[str, Any]) -> list[str]:
    risks = []
    if _safe_float(fund.get("std_dev"), 0) > 20:
        risks.append("High volatility versus diversified large-cap funds.")
    if _safe_float(fund.get("downside_capture"), 0) > 100:
        risks.append("Downside capture is high in mock data.")
    if str(fund.get("risk")) in {"high", "very_high"}:
        risks.append("Not suitable for short horizons or conservative risk profiles.")
    return risks or ["No major fund risk flagged by available mock data."]


def _base_data_warnings() -> list[str]:
    return [
        "Live news, insider activity, analyst changes and latest filings are not fully connected.",
        "Use live broker/API data before making any real-money decision.",
    ]


def _safe_float(value: Any, default: float) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
