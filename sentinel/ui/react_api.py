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
from sentinel.data.mf_advisor import MFAdvisor
from sentinel.data.mock_data import ALL_MOCK_STOCKS
from sentinel.ops.deployment_readiness import build_deployment_readiness_report
from sentinel.ops.killswitch import get_kill_state
from sentinel.reports.morning_brief import MorningBrief
from sentinel.research.sprint7_factory import build_sprint7_research_snapshot
from sentinel.screeners.runner import ScreenerRunner

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = ROOT / "frontend" / "dist"
MOCK_MODE = os.getenv("MOCK_MODE", "true").lower() == "true"
_CACHE: dict[str, tuple[float, Any]] = {}
_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9&-]{2,20}$")
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
    return {
        "app": "Project Sentinel",
        "generated_at": utc_now(),
        "mock_mode": MOCK_MODE,
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
    return {
        "overlay": overlay,
        "rates": rates,
        "calendar": connector.get_economic_calendar(days_ahead=7),
        "health": connector.health_check(),
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


def _validated_symbol(raw_symbol: str) -> str:
    symbol = str(raw_symbol or "").upper().strip()
    if not _SYMBOL_PATTERN.fullmatch(symbol):
        raise ApiError(
            HTTPStatus.BAD_REQUEST,
            "Symbol must be 2-20 uppercase letters/numbers and may include & or hyphen.",
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


ROUTES = {
    "/api/status": lambda: _cached("status", 10, _status_payload),
    "/api/morning-brief": lambda: _cached("morning", 300, lambda: MorningBrief().generate()),
    "/api/screeners": lambda: _cached("screeners", 300, lambda: ScreenerRunner().run_all()),
    "/api/strategy-factory": lambda: _cached("strategy", 300, _strategy_payload),
    "/api/forex": lambda: _cached("forex", 120, _forex_payload),
    "/api/guardrails": lambda: _cached("guardrails", 30, lambda: GuardrailEngine().get_dashboard_summary()),
    "/api/readiness": lambda: _cached("readiness", 60, lambda: build_deployment_readiness_report(_profile()).as_dict()),
    "/api/symbols": lambda: _cached("symbols", 300, lambda: {"equities": _equity_symbols()}),
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
