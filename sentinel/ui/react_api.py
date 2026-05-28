"""
Local JSON API for the React Sentinel dashboard.

The React UI is intentionally a presentation layer. All trading, research,
risk, and readiness logic remains in the Python backend.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from sentinel.core.config import OperatorProfile, load_config
from sentinel.core.guardrails import GuardrailEngine
from sentinel.core.types import utc_now
from sentinel.data.forex_connector import ForexConnector
from sentinel.data.fundamental_store import FundamentalStore
from sentinel.data.historical_store import HistoricalStore
from sentinel.data.market_data import MarketDataStore
from sentinel.data.mf_advisor import MFAdvisor
from sentinel.data.mock_data import ALL_MOCK_STOCKS
from sentinel.fo.covered_call import CoveredCallCandidate, CoveredCallPlanner, EquityHolding
from sentinel.fo.greeks_dashboard import OptionContract, calculate_greeks
from sentinel.ops.deployment_readiness import build_deployment_readiness_report
from sentinel.ops.killswitch import get_kill_state
from sentinel.reports.morning_brief import MorningBrief
from sentinel.research.decision_support import (
    ResearchRequest,
    build_research_report,
    supported_assets,
)
from sentinel.research.sprint7_factory import build_sprint7_research_snapshot
from sentinel.screeners.runner import ScreenerRunner

logger = logging.getLogger(__name__)

PRODUCT_MISSION = (
    "AI-powered Trading Research Assistant for structured market homework, "
    "risk planning, scenario analysis, and manual execution support."
)
PRODUCT_BOUNDARY = (
    "Sentinel does not execute trades automatically and must not be treated as "
    "a guaranteed prediction or blind buy/sell system."
)
ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = ROOT / "frontend" / "dist"
MOCK_MODE = os.getenv("MOCK_MODE", "true").lower() == "true"
_CACHE: dict[str, tuple[float, Any]] = {}
_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9&_-]{2,32}$")
_ALLOWED_ORIGINS = {
    "http://127.0.0.1:8765",
    "http://localhost:8765",
    "http://127.0.0.1:5173",
    "http://localhost:5173",
}


class ApiError(Exception):
    """Client-facing API error with an explicit HTTP status."""

    def __init__(self, status: HTTPStatus, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.details = details or {}


def _cached(key: str, ttl_seconds: int, producer: Any) -> Any:
    now = time.time()
    cached = _CACHE.get(key)
    if cached and now - cached[0] < ttl_seconds:
        return cached[1]
    value = producer()
    _CACHE[key] = (now, value)
    return value


def _profile() -> OperatorProfile:
    try:
        return load_config()
    except Exception as exc:
        logger.warning("Falling back to default operator profile: %s", exc)
        return OperatorProfile()


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def _profile_payload(profile: OperatorProfile) -> dict[str, Any]:
    return {
        "operator_name": profile.operator_name,
        "location": profile.location,
        "trading_stage": profile.trading_stage,
        "total_portfolio_value_inr": profile.total_portfolio_value_inr,
        "max_risk_per_trade_inr": profile.max_risk_per_trade_inr,
        "max_total_open_risk_inr": profile.max_total_open_risk_inr,
        "emergency_fund_months_confirmed": profile.emergency_fund_months_confirmed,
        "live_trading_enabled": profile.is_live_trading_enabled,
        "paper_mode": profile.is_paper_mode,
        "sprint6_blockers": profile.validate_sprint6_ready(),
        "risk": {
            "max_risk_per_trade_pct": profile.risk.max_risk_per_trade_pct,
            "max_total_open_risk_pct": profile.risk.max_total_open_risk_pct,
            "min_risk_reward_ratio": profile.risk.min_risk_reward_ratio,
            "vix_defensive_threshold": profile.risk.vix_defensive_threshold,
        },
    }


def _status_payload() -> dict[str, Any]:
    profile = _profile()
    readiness = build_deployment_readiness_report(profile)
    historical = HistoricalStore()
    data_warning = (
        "MOCK_MODE=true. Dashboard market numbers are simulated and must not be "
        "used as live prices."
        if MOCK_MODE
        else (
            "Partial live mode. Verify provider health and timestamps before trading; "
            "some Indian market internals still require live provider integration."
        )
    )
    return {
        "app": "Project Sentinel",
        "mission": PRODUCT_MISSION,
        "safety_boundary": PRODUCT_BOUNDARY,
        "auto_execution_enabled": False,
        "generated_at": utc_now(),
        "mock_mode": MOCK_MODE,
        "data_quality": {
            "mode": "mock" if MOCK_MODE else "live",
            "is_live": False if MOCK_MODE else "partial",
            "warning": data_warning,
            "limitations": [
                "NSE market internals are not yet wired to a live NSE provider.",
                "NSDL FII/DII flows are not yet wired to a live NSDL provider.",
                "Live forex requires TWELVE_DATA_API_KEY; live macro requires FRED_API_KEY.",
            ],
        },
        "profile": _profile_payload(profile),
        "kill_switch": get_kill_state(),
        "readiness": readiness.as_dict(),
        "data": {
            "symbols_with_ohlcv": len(historical.get_available_symbols()),
            "equity_symbols": _equity_symbols(),
        },
    }


def _equity_symbols() -> list[dict[str, str]]:
    return [
        {
            "symbol": symbol,
            "name": str(info.get("name", "")),
            "sector": str(info.get("sector", "")),
        }
        for symbol, info in sorted(ALL_MOCK_STOCKS.items())
    ]


def _strategy_payload() -> dict[str, Any]:
    snapshot = build_sprint7_research_snapshot(_profile())
    return {
        "generated_at": snapshot.generated_at,
        "stage": snapshot.stage,
        "promotion_status": snapshot.promotion_status,
        "live_approved": snapshot.live_approved,
        "allocation_method": snapshot.allocation_method,
        "strategy_metrics": snapshot.strategy_metrics,
        "target_weights": snapshot.target_weights,
        "correlation_matrix": snapshot.correlation_matrix,
        "high_correlation_pairs": snapshot.high_correlation_pairs,
        "gates": snapshot.gates,
        "promotion_memo": snapshot.promotion_memo,
    }


def _forex_payload() -> dict[str, Any]:
    connector = ForexConnector()
    overlay = connector.get_macro_overlay()
    pairs = ["USDINR", "EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
    rates = []
    for pair in pairs:
        try:
            rates.append(connector.get_live_forex_rate(pair))
        except Exception as exc:
            rates.append({"pair": pair, "error": str(exc)})
    health = connector.health_check()
    return {
        "overlay": overlay,
        "rates": rates,
        "calendar": connector.get_economic_calendar(days_ahead=7),
        "health": health,
        "data_quality": {
            "mode": "mock" if health.get("mock_mode") else "live",
            "is_live": not bool(health.get("mock_mode")),
            "warning": (
                "MOCK_MODE=true. Forex and macro values are simulated and will "
                "not match live markets."
                if health.get("mock_mode")
                else "Live forex mode. Check provider keys, errors, and timestamps before trading."
            ),
        },
    }


def _data_health_payload() -> dict[str, Any]:
    market = MarketDataStore()
    internals = market.get_market_internals()
    forex = ForexConnector().health_check()
    return {
        "generated_at": utc_now(),
        "overall_mode": "mock" if MOCK_MODE else "live",
        "checks": [
            {
                "name": "System mode",
                "passed": not MOCK_MODE,
                "status": "mock" if MOCK_MODE else "live",
                "detail": (
                    "MOCK_MODE=true; market values are simulated."
                    if MOCK_MODE
                    else "MOCK_MODE=false; live providers are required."
                ),
            },
            {
                "name": "Forex live provider",
                "passed": (not forex.get("mock_mode") and forex.get("twelve_data_key_set")),
                "status": "configured" if forex.get("twelve_data_key_set") else "missing",
                "detail": "Twelve Data key is required for live forex rates.",
            },
            {
                "name": "Macro live provider",
                "passed": (not forex.get("mock_mode") and forex.get("fred_key_set")),
                "status": "configured" if forex.get("fred_key_set") else "missing",
                "detail": "FRED key is required for live US macro inputs.",
            },
            {
                "name": "NSE market internals",
                "passed": bool(internals.get("available")) and not MOCK_MODE,
                "status": "available" if internals.get("available") and not MOCK_MODE else "unavailable",
                "detail": "Nifty, India VIX, breadth and PCR must come from a live NSE provider.",
            },
            {
                "name": "NSDL FII/DII flows",
                "passed": bool(market.get_latest_fii_dii()) and not MOCK_MODE,
                "status": "stored" if market.get_latest_fii_dii() and not MOCK_MODE else "unavailable",
                "detail": "Institutional flow data must come from a live NSDL/provider feed.",
            },
        ],
    }


def _options_payload(query: dict[str, list[str]]) -> dict[str, Any]:
    underlying = _validated_symbol(query.get("underlying", ["RELIANCE"])[0])
    option_type = query.get("option_type", ["call"])[0].strip().lower()
    if option_type not in {"call", "put"}:
        raise ApiError(
            HTTPStatus.BAD_REQUEST,
            "option_type must be call or put.",
            {"option_type": option_type},
        )
    strike = _float_param(query, "strike", 3100.0, min_value=1.0, max_value=1_000_000.0)
    expiry_days = _int_param(query, "expiry_days", 30, min_value=1, max_value=365)
    lot_size = _int_param(query, "lot_size", 250, min_value=1, max_value=100000)
    last_price = _float_param(query, "last_price", 42.0, min_value=0.01, max_value=1_000_000.0)
    implied_vol_pct = _float_param(query, "implied_vol_pct", 22.0, min_value=0.01, max_value=500.0)
    underlying_price = _float_param(query, "underlying_price", 2950.0, min_value=0.01, max_value=1_000_000.0)
    holding_qty = _int_param(query, "holding_qty", 0, min_value=0, max_value=10000000)
    holding_avg = _float_param(query, "holding_avg", underlying_price, min_value=0.01, max_value=1_000_000.0)
    portfolio_value = _float_param(query, "portfolio_value", 300000.0, min_value=1000.0, max_value=1_000_000_000.0)
    requested_lots = _int_param(query, "requested_lots", 1, min_value=1, max_value=10000)

    contract = OptionContract(
        symbol=_option_symbol(underlying, expiry_days, strike, option_type),
        underlying=underlying,
        option_type=option_type,
        strike=strike,
        expiry_days=expiry_days,
        lot_size=lot_size,
        last_price=last_price,
        implied_vol_pct=implied_vol_pct,
    )
    greeks = calculate_greeks(contract, underlying_price)
    holding = EquityHolding(
        symbol=underlying,
        quantity=holding_qty,
        average_price=holding_avg,
        last_price=underlying_price,
    )
    hedge = CoveredCallPlanner(portfolio_value=portfolio_value).evaluate(
        holding,
        contract,
        requested_lots=requested_lots,
    )
    hedge_payload = _hedge_payload(hedge)
    payoff = _covered_call_payoff(holding, contract, requested_lots, hedge_payload)
    gates = _options_gates(hedge_payload, contract, holding_qty, payoff)

    return {
        "generated_at": utc_now(),
        "mode": "covered_call_review",
        "research_only": True,
        "operator_warning": (
            "Options Lab is decision support only. Naked weekly directional options "
            "are not enabled; final execution remains with the trader."
        ),
        "data_quality": {
            "mode": "mock" if MOCK_MODE else "live",
            "is_live": not MOCK_MODE,
            "warning": (
                "Input values are manual/simulated. Confirm live option chain, bid/ask, "
                "OI, IV, liquidity and margin in the broker before execution."
            ),
        },
        "contract": contract,
        "holding": holding,
        "greeks_snapshot": greeks,
        "hedge_review": hedge_payload,
        "payoff": payoff,
        "safety_gates": gates,
        "final_decision": _options_final_decision(gates),
    }


def _option_symbol(underlying: str, expiry_days: int, strike: float, option_type: str) -> str:
    suffix = "CE" if option_type == "call" else "PE"
    strike_text = str(int(strike)) if float(strike).is_integer() else str(strike).replace(".", "P")
    return f"{underlying}{expiry_days}D{strike_text}{suffix}"


def _hedge_payload(hedge: Any) -> dict[str, Any]:
    if isinstance(hedge, CoveredCallCandidate):
        return {
            "status": "approved_for_review",
            "is_hedging_only": hedge.is_hedging_only,
            "lots": hedge.lots,
            "premium_income": hedge.premium_income,
            "notional_exposure": hedge.notional_exposure,
            "notional_exposure_pct": hedge.notional_exposure_pct,
            "max_covered_quantity": hedge.max_covered_quantity,
            "warnings": hedge.warnings,
        }
    return {
        "status": "rejected",
        "is_hedging_only": False,
        "reason": hedge.reason,
        "warnings": [hedge.reason],
    }


def _covered_call_payoff(
    holding: EquityHolding,
    contract: OptionContract,
    requested_lots: int,
    hedge_payload: dict[str, Any],
) -> dict[str, Any]:
    if hedge_payload["status"] != "approved_for_review":
        return {
            "available": False,
            "reason": "Payoff shown only for approved covered-call reviews.",
        }
    lots = int(hedge_payload["lots"])
    covered_qty = lots * contract.lot_size
    premium = contract.last_price * covered_qty
    breakeven = max(0.0, holding.average_price - contract.last_price)
    called_away_profit = (contract.strike - holding.average_price) * covered_qty + premium
    downside_at_minus_5_pct = ((holding.last_price * 0.95) - holding.average_price) * covered_qty + premium
    downside_at_minus_10_pct = ((holding.last_price * 0.90) - holding.average_price) * covered_qty + premium
    return {
        "available": True,
        "covered_quantity": covered_qty,
        "premium_income": round(premium, 2),
        "breakeven_after_premium": round(breakeven, 2),
        "profit_if_called_away": round(called_away_profit, 2),
        "estimated_pnl_if_underlying_minus_5_pct": round(downside_at_minus_5_pct, 2),
        "estimated_pnl_if_underlying_minus_10_pct": round(downside_at_minus_10_pct, 2),
        "max_loss_note": "Covered call still carries equity downside below adjusted breakeven.",
    }


def _options_gates(
    hedge_payload: dict[str, Any],
    contract: OptionContract,
    holding_qty: int,
    payoff: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        {
            "name": "Hedging only",
            "passed": hedge_payload.get("is_hedging_only") is True,
            "detail": "Covered call must be backed by held equity. Naked options are blocked.",
        },
        {
            "name": "No weekly expiry",
            "passed": not contract.is_weekly,
            "detail": "Weekly or expiry-day entries are blocked for this workflow.",
        },
        {
            "name": "Call-only strategy",
            "passed": contract.option_type == "call",
            "detail": "This workflow supports covered calls only, not naked puts/calls.",
        },
        {
            "name": "Sufficient holding",
            "passed": holding_qty >= contract.lot_size,
            "detail": f"Need at least {contract.lot_size} shares for one covered lot.",
        },
        {
            "name": "Payoff visible",
            "passed": bool(payoff.get("available")),
            "detail": "Premium, breakeven and downside scenarios must be visible before execution.",
        },
    ]


def _options_final_decision(gates: list[dict[str, Any]]) -> dict[str, Any]:
    blockers = [gate for gate in gates if not gate["passed"]]
    if blockers:
        return {
            "category": "Blocked",
            "explanation": "Do not execute. One or more options safety gates failed.",
            "blockers": [gate["name"] for gate in blockers],
        }
    return {
        "category": "Manual Review Candidate",
        "explanation": (
            "Covered-call review passes structural gates. Confirm live option chain, spreads, "
            "OI, margin, tax impact and thesis before manual execution."
        ),
        "blockers": [],
    }


def _fundamentals_payload(query: dict[str, list[str]]) -> dict[str, Any]:
    symbol = _validated_symbol(query.get("symbol", ["RELIANCE"])[0])
    store = FundamentalStore()
    if MOCK_MODE and symbol not in ALL_MOCK_STOCKS:
        raise ApiError(
            HTTPStatus.BAD_REQUEST,
            f"{symbol} is not in the supported NSE equity universe.",
            {"symbol": symbol},
        )
    if not store.ingest(symbol):
        raise ApiError(
            HTTPStatus.NOT_FOUND,
            f"No fundamentals are available for {symbol}.",
            {"symbol": symbol},
        )
    return {
        "symbol": symbol,
        "quality": store.compute_quality_score(symbol),
        "latest": store.get_latest(symbol),
    }


def _mf_payload(query: dict[str, list[str]]) -> dict[str, Any]:
    budget = _int_param(query, "budget", 3000, min_value=500, max_value=100000)
    risk = query.get("risk", ["moderate"])[0].strip().lower()
    if risk not in {"conservative", "moderate", "aggressive"}:
        raise ApiError(
            HTTPStatus.BAD_REQUEST,
            "Risk must be conservative, moderate, or aggressive.",
            {"risk": risk},
        )
    horizon = _int_param(query, "horizon", 10, min_value=3, max_value=30)
    advisor = MFAdvisor()
    return {
        "recommendation": advisor.recommend_sip(budget, risk, horizon),
        "scores": advisor.score_all_funds(),
    }


def _research_payload(query: dict[str, list[str]]) -> dict[str, Any]:
    asset_type = query.get("asset_type", ["equity"])[0].strip().lower()
    if asset_type not in {"equity", "forex", "mutual_fund"}:
        raise ApiError(
            HTTPStatus.BAD_REQUEST,
            "asset_type must be equity, forex, or mutual_fund.",
            {"asset_type": asset_type},
        )
    symbol = _validated_symbol(query.get("symbol", ["RELIANCE"])[0])
    horizon = query.get("horizon", ["swing"])[0].strip().lower()
    if horizon not in {"intraday", "swing", "positional", "long-term"}:
        raise ApiError(
            HTTPStatus.BAD_REQUEST,
            "horizon must be intraday, swing, positional, or long-term.",
            {"horizon": horizon},
        )
    capital = None
    if query.get("capital_inr", [""])[0]:
        capital = float(_int_param(query, "capital_inr", 0, min_value=500, max_value=100000000))
    try:
        return build_research_report(
            ResearchRequest(
                asset_type=asset_type,
                symbol=symbol,
                horizon=horizon,
                capital_inr=capital,
            ),
            _profile(),
        )
    except ValueError as exc:
        raise ApiError(HTTPStatus.BAD_REQUEST, str(exc), {"symbol": symbol})


def _final_shortlist_payload(query: dict[str, list[str]]) -> dict[str, Any]:
    limit = _int_param(query, "limit", 3, min_value=1, max_value=10)
    requested_symbols = _requested_equity_symbols(query)
    screener_snapshot = _cached("screeners", 120, lambda: ScreenerRunner().run_all())
    candidate_map = _equity_candidate_map(screener_snapshot)
    symbols = _shortlist_symbols(requested_symbols, candidate_map, limit)
    profile = _profile()
    entries = []

    for symbol in symbols[:limit]:
        report = build_research_report(
            ResearchRequest(
                asset_type="equity",
                symbol=symbol,
                horizon="swing",
                capital_inr=float(profile.max_risk_per_trade_inr),
            ),
            profile,
        )
        entries.append(
            _shortlist_entry_from_report(
                report=report,
                rank=len(entries) + 1,
                candidate=candidate_map.get(symbol),
            )
        )

    return {
        "generated_at": utc_now(),
        "title": "Final Shortlist",
        "research_only": True,
        "auto_execution_enabled": False,
        "selection_method": (
            "User watchlist plus screener-confirmed equities, ranked by conviction and research checks."
            if requested_symbols
            else "Screener-confirmed equities first, then liquid default watchlist for morning review."
        ),
        "operator_warning": (
            "This shortlist is decision support only. Confirm live price, volume, spreads, news, "
            "market breadth and your risk limits before placing any manual order."
        ),
        "data_quality": {
            "mode": "mock" if MOCK_MODE else "live",
            "is_live": not MOCK_MODE,
            "warning": (
                "MOCK_MODE=true. Shortlist levels are simulated and will not match live markets."
                if MOCK_MODE
                else "Live/partial-live mode. Use only entries with fresh provider timestamps."
            ),
            "live_market_confirmation_required": True,
        },
        "screeners_summary": screener_snapshot.get("_summary", {}),
        "entries": entries,
    }


def _requested_equity_symbols(query: dict[str, list[str]]) -> list[str]:
    raw_values = query.get("symbols", [])
    symbols: list[str] = []
    for raw_value in raw_values:
        for part in str(raw_value).split(","):
            if not part.strip():
                continue
            symbol = _validated_symbol(part)
            if symbol not in ALL_MOCK_STOCKS:
                raise ApiError(
                    HTTPStatus.BAD_REQUEST,
                    f"{symbol} is not in the supported NSE equity universe.",
                    {"symbol": symbol},
                )
            if symbol not in symbols:
                symbols.append(symbol)
    return symbols[:10]


def _equity_candidate_map(screeners: dict[str, Any]) -> dict[str, dict[str, Any]]:
    aggregated: dict[str, dict[str, Any]] = {}
    for screener_name, result in screeners.items():
        if screener_name.startswith("_") or not isinstance(result, dict):
            continue
        for candidate in result.get("candidates", []):
            symbol = str(candidate.get("symbol", "")).upper()
            if symbol not in ALL_MOCK_STOCKS:
                continue
            existing = aggregated.get(symbol)
            source = str(candidate.get("screener") or screener_name)
            if existing is None or float(candidate.get("conviction_score") or 0) > float(existing.get("conviction_score") or 0):
                aggregated[symbol] = {**candidate, "source_screeners": [source]}
            elif source not in existing.setdefault("source_screeners", []):
                existing["source_screeners"].append(source)
    return dict(
        sorted(
            aggregated.items(),
            key=lambda item: float(item[1].get("conviction_score") or 0),
            reverse=True,
        )
    )


def _shortlist_symbols(
    requested_symbols: list[str],
    candidate_map: dict[str, dict[str, Any]],
    limit: int,
) -> list[str]:
    default_watchlist = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "LT", "SBIN", "BHARTIARTL"]
    symbols: list[str] = []
    for source in (requested_symbols, list(candidate_map), default_watchlist):
        for symbol in source:
            if symbol in ALL_MOCK_STOCKS and symbol not in symbols:
                symbols.append(symbol)
            if len(symbols) >= limit:
                return symbols
    return symbols


def _shortlist_entry_from_report(
    report: dict[str, Any],
    rank: int,
    candidate: dict[str, Any] | None,
) -> dict[str, Any]:
    overview = report["sections"]["C. Asset Overview"]
    plan = report["sections"]["G. Trade or Investment Plan"]
    decision = report["sections"]["I. Final Decision"]
    summary = report["sections"]["A. Executive Summary"]
    data_quality = report["sections"]["J. Data Quality Warning"]
    action = _shortlist_action(decision.get("action_category"), candidate)
    entry_zone = _candidate_or_plan(candidate, "entry_low", plan.get("entry_zone"))
    if candidate and candidate.get("entry_low") is not None and candidate.get("entry_high") is not None:
        entry_zone = [candidate.get("entry_low"), candidate.get("entry_high")]
    risk = _shortlist_risk(summary, candidate)

    return {
        "rank": rank,
        "symbol": report["symbol"],
        "name": overview.get("name"),
        "category": overview.get("category"),
        "sector": ALL_MOCK_STOCKS.get(report["symbol"], {}).get("sector"),
        "latest_available_price": overview.get("current_price_or_latest_available"),
        "data_timestamp": overview.get("data_timestamp"),
        "source_screeners": candidate.get("source_screeners", []) if candidate else [],
        "action": action,
        "entry_zone": entry_zone if action != "Avoid" else None,
        "stop_loss": candidate.get("stop_loss") if candidate else plan.get("stop_loss"),
        "target_1": candidate.get("target_1") if candidate else plan.get("target_1"),
        "target_2": candidate.get("target_2") if candidate else plan.get("target_2"),
        "risk_reward": candidate.get("rr_ratio") if candidate else plan.get("risk_reward_ratio"),
        "confidence": _shortlist_confidence(decision, candidate),
        "risk": risk,
        "risk_score": _shortlist_risk_score(risk, decision),
        "suitability": decision.get("suitability"),
        "reason": _shortlist_reason(report, candidate, action),
        "warnings": _shortlist_warnings(candidate, data_quality),
        "live_market_confirmation_required": bool(data_quality.get("live_market_confirmation_required")),
    }


def _candidate_or_plan(candidate: dict[str, Any] | None, key: str, fallback: Any) -> Any:
    if candidate and candidate.get(key) is not None:
        return candidate[key]
    return fallback


def _shortlist_action(action_category: Any, candidate: dict[str, Any] | None) -> str:
    if candidate:
        score = float(candidate.get("conviction_score") or 0)
        rr = float(candidate.get("rr_ratio") or 0)
        if score >= 75 and rr >= 2:
            return "Buy only above confirmation level"
        if score >= 65 and rr >= 2:
            return "Wait for pullback"
    mapping = {
        "Strong Watchlist Candidate": "Strong watchlist candidate",
        "Buy Only Above Confirmation Level": "Buy only above confirmation level",
        "Accumulate on Dips": "Accumulate on dips",
        "Short-Term Trade Candidate": "Short-term trade candidate",
        "Long-Term Investment Candidate": "Long-term investment candidate",
        "Avoid": "Avoid",
        "High Risk / Speculative": "High risk / speculative",
        "Wait for Better Entry": "Wait for better entry",
    }
    return mapping.get(str(action_category), "Wait for better entry")


def _shortlist_confidence(decision: dict[str, Any], candidate: dict[str, Any] | None) -> float:
    if candidate and candidate.get("conviction_score") is not None:
        return round(min(max(float(candidate["conviction_score"]) / 10, 0), 10), 1)
    return round(float(decision.get("confidence_score_out_of_10") or 0), 1)


def _shortlist_risk(summary: dict[str, Any], candidate: dict[str, Any] | None) -> str:
    if not candidate:
        return str(summary.get("risk_level") or "Medium")
    score = float(candidate.get("conviction_score") or 0)
    rr = float(candidate.get("rr_ratio") or 0)
    if rr < 2:
        return "High"
    if score >= 80:
        return "Medium"
    if score >= 70:
        return "Medium-High"
    return "High"


def _shortlist_risk_score(risk: str, decision: dict[str, Any]) -> int:
    mapping = {"Low": 3, "Medium": 5, "Medium-High": 6, "High": 8, "Very High": 9}
    return mapping.get(risk, int(decision.get("risk_score_out_of_10") or 6))


def _shortlist_reason(report: dict[str, Any], candidate: dict[str, Any] | None, action: str) -> str:
    if candidate and candidate.get("thesis"):
        return _trim_text(str(candidate["thesis"]), 260)
    sections = report["sections"]
    final_explanation = str(sections["I. Final Decision"].get("final_explanation", ""))
    trend = str(sections["E. Technical View"].get("current_trend", ""))
    strengths = sections["D. Fundamental View"].get("key_strengths", [])
    weaknesses = sections["D. Fundamental View"].get("key_weaknesses", [])
    if action == "Avoid":
        risk_text = ("; ".join(map(str, weaknesses[:2])) or "risk-reward or confirmation is weak").rstrip(".")
        reason = f"{final_explanation} Key risks: {risk_text}."
    else:
        reason = f"{final_explanation} Trend: {trend}. Strengths: {'; '.join(map(str, strengths[:2])) or 'needs live confirmation'}."
    return _trim_text(reason, 260)


def _shortlist_warnings(candidate: dict[str, Any] | None, data_quality: dict[str, Any]) -> list[str]:
    warnings = []
    if candidate:
        warnings.extend(str(item) for item in candidate.get("risks", [])[:3])
    for key in ("missing_data", "stale_data", "assumptions"):
        value = data_quality.get(key, [])
        if isinstance(value, list):
            warnings.extend(str(item) for item in value[:2])
    return list(dict.fromkeys(warnings))[:6]


def _trim_text(value: str, max_length: int) -> str:
    compact = " ".join(value.split())
    if len(compact) <= max_length:
        return compact
    return f"{compact[: max_length - 3].rstrip()}..."


def _validated_symbol(raw_symbol: str) -> str:
    symbol = str(raw_symbol or "").upper().strip()
    if not _SYMBOL_PATTERN.fullmatch(symbol):
        raise ApiError(
            HTTPStatus.BAD_REQUEST,
            "Symbol must be 2-32 uppercase letters/numbers and may include &, underscore, or hyphen.",
            {"symbol": symbol},
        )
    return symbol


def _int_param(
    query: dict[str, list[str]],
    name: str,
    default: int,
    min_value: int,
    max_value: int,
) -> int:
    raw_value = query.get(name, [str(default)])[0]
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        raise ApiError(
            HTTPStatus.BAD_REQUEST,
            f"{name} must be a whole number.",
            {name: str(raw_value)},
        )
    if not min_value <= value <= max_value:
        raise ApiError(
            HTTPStatus.BAD_REQUEST,
            f"{name} must be between {min_value} and {max_value}.",
            {name: value},
        )
    return value


def _float_param(
    query: dict[str, list[str]],
    name: str,
    default: float,
    min_value: float,
    max_value: float,
) -> float:
    raw_value = query.get(name, [str(default)])[0]
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        raise ApiError(
            HTTPStatus.BAD_REQUEST,
            f"{name} must be a number.",
            {name: str(raw_value)},
        )
    if not min_value <= value <= max_value:
        raise ApiError(
            HTTPStatus.BAD_REQUEST,
            f"{name} must be between {min_value} and {max_value}.",
            {name: value},
        )
    return value


ROUTES = {
    "/api/status": lambda: _cached("status", 10, _status_payload),
    "/api/morning-brief": lambda: _cached("morning", 300, lambda: MorningBrief().generate()),
    "/api/screeners": lambda: _cached("screeners", 300, lambda: ScreenerRunner().run_all()),
    "/api/strategy-factory": lambda: _cached("strategy", 300, _strategy_payload),
    "/api/forex": lambda: _cached("forex", 120, _forex_payload),
    "/api/guardrails": lambda: _cached("guardrails", 30, lambda: GuardrailEngine().get_dashboard_summary()),
    "/api/readiness": lambda: _cached("readiness", 60, lambda: build_deployment_readiness_report(_profile()).as_dict()),
    "/api/symbols": lambda: _cached("symbols", 300, lambda: {"equities": _equity_symbols()}),
    "/api/research-assets": lambda: _cached("research_assets", 300, supported_assets),
    "/api/data-health": lambda: _cached("data_health", 30, _data_health_payload),
}


class SentinelApiHandler(SimpleHTTPRequestHandler):
    server_version = "SentinelReactAPI/1.0"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(FRONTEND_DIST), **kwargs)

    def end_headers(self) -> None:
        origin = self.headers.get("Origin")
        if origin in _ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/api/fundamentals":
                self._send_json(_fundamentals_payload(query))
                return
            if parsed.path == "/api/mf-advisor":
                self._send_json(_mf_payload(query))
                return
            if parsed.path == "/api/research":
                self._send_json(_research_payload(query))
                return
            if parsed.path == "/api/final-shortlist":
                self._send_json(_final_shortlist_payload(query))
                return
            if parsed.path == "/api/options-review":
                self._send_json(_options_payload(query))
                return
            if parsed.path in ROUTES:
                self._send_json(ROUTES[parsed.path]())
                return
            self._serve_spa(parsed.path)
        except ApiError as exc:
            self._send_json({"error": exc.message, **exc.details}, exc.status)
        except Exception as exc:
            logger.exception("API request failed")
            self._send_json({"error": "Internal server error", "detail": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        try:
            body = json.dumps(_json_safe(payload), ensure_ascii=False).encode("utf-8")
            self.send_response(status)
        except Exception as exc:
            logger.exception("API request failed")
            body = json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8")
            self.send_response(HTTPStatus.INTERNAL_SERVER_ERROR)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_spa(self, path: str) -> None:
        if not FRONTEND_DIST.exists():
            self._send_json(
                {
                    "error": "React build not found.",
                    "hint": "Run `npm install` and `npm run build` inside the frontend folder.",
                },
                HTTPStatus.NOT_FOUND,
            )
            return
        safe_path = unquote(path)
        if ".." in Path(safe_path).parts:
            self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        root = FRONTEND_DIST.resolve()
        requested = (FRONTEND_DIST / safe_path.lstrip("/")).resolve()
        if root in requested.parents and requested.exists() and requested.is_file():
            super().do_GET()
            return
        self.path = "/index.html"
        super().do_GET()


def run(host: str = "127.0.0.1", port: int = 8765) -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    server = ThreadingHTTPServer((host, port), SentinelApiHandler)
    logger.info("Sentinel React API running at http://%s:%s", host, port)
    server.serve_forever()


if __name__ == "__main__":
    run()
