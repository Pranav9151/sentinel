from http import HTTPStatus

import pytest

import sentinel.data.market_data as market_data
from sentinel.data.market_data import MarketDataStore
from sentinel.ui.react_api import (
    ApiError,
    _equity_symbols,
    _fundamentals_payload,
    _mf_payload,
    _options_payload,
    _research_payload,
    _forex_payload,
    _final_shortlist_payload,
    _status_payload,
    _data_health_payload,
    _validated_symbol,
)


EXPECTED_RESEARCH_SECTIONS = [
    "A. Executive Summary",
    "B. Market Context",
    "C. Asset Overview",
    "D. Fundamental View",
    "E. Technical View",
    "F. News and Sentiment View",
    "G. Trade or Investment Plan",
    "H. Scenario Analysis",
    "I. Final Decision",
    "J. Data Quality Warning",
]


class TestReactApiValidation:
    def test_valid_symbol_is_normalized(self):
        assert _validated_symbol(" reliance ") == "RELIANCE"

    def test_invalid_symbol_shape_is_rejected(self):
        with pytest.raises(ApiError) as exc:
            _validated_symbol("<script>")

        assert exc.value.status == HTTPStatus.BAD_REQUEST

    def test_unknown_fundamental_symbol_is_rejected(self):
        with pytest.raises(ApiError) as exc:
            _fundamentals_payload({"symbol": ["NOTLISTED999"]})

        assert exc.value.status == HTTPStatus.BAD_REQUEST
        assert "supported NSE equity universe" in exc.value.message

    def test_valid_fundamental_symbol_returns_quality(self):
        payload = _fundamentals_payload({"symbol": ["RELIANCE"]})

        assert payload["symbol"] == "RELIANCE"
        assert payload["quality"]["quality_score"] is not None

    def test_mock_symbol_list_uses_uppercase_symbols(self):
        symbols = {item["symbol"] for item in _equity_symbols()}

        assert "DIXON" in symbols
        assert "Dixon" not in symbols

    def test_status_payload_exposes_data_quality_warning(self):
        payload = _status_payload()

        assert payload["auto_execution_enabled"] is False
        assert "Research Assistant" in payload["mission"]
        assert payload["data_quality"]["mode"] in {"mock", "live"}
        assert "warning" in payload["data_quality"]

    def test_forex_payload_exposes_data_quality_warning(self):
        payload = _forex_payload()

        assert payload["data_quality"]["mode"] in {"mock", "live"}
        assert "warning" in payload["data_quality"]

    def test_data_health_exposes_provider_checks(self):
        payload = _data_health_payload()

        names = {check["name"] for check in payload["checks"]}
        assert "System mode" in names
        assert "NSE market internals" in names
        assert "NSDL FII/DII flows" in names

    def test_live_market_internals_fail_closed_without_provider(self, monkeypatch):
        monkeypatch.setattr(market_data, "MOCK_MODE", False)
        store = MarketDataStore()

        internals = store.get_market_internals()
        bias = store.get_market_bias()

        assert internals["available"] is False
        assert internals["nifty50_close"] is None
        assert bias["bias"] == "UNKNOWN"
        assert bias["score"] is None

    def test_live_fii_ingest_fails_closed_without_provider(self, monkeypatch):
        monkeypatch.setattr(market_data, "MOCK_MODE", False)
        store = MarketDataStore()

        assert store.ingest_fii_dii() is False

    def test_mf_rejects_bad_risk_value(self):
        with pytest.raises(ApiError) as exc:
            _mf_payload({"risk": ["reckless"]})

        assert exc.value.status == HTTPStatus.BAD_REQUEST

    def test_mf_rejects_budget_outside_bounds(self):
        with pytest.raises(ApiError) as exc:
            _mf_payload({"budget": ["1"]})

        assert exc.value.status == HTTPStatus.BAD_REQUEST

    def test_mf_rejects_non_numeric_horizon(self):
        with pytest.raises(ApiError) as exc:
            _mf_payload({"horizon": ["forever"]})

        assert exc.value.status == HTTPStatus.BAD_REQUEST

    def test_research_equity_returns_a_to_j_decision_support_report(self):
        payload = _research_payload(
            {
                "asset_type": ["equity"],
                "symbol": ["RELIANCE"],
                "horizon": ["swing"],
                "capital_inr": ["1000"],
            }
        )

        assert payload["research_only"] is True
        assert payload["asset_type"] == "equity"
        assert list(payload["sections"]) == EXPECTED_RESEARCH_SECTIONS
        assert payload["sections"]["I. Final Decision"]["action_category"] in {
            "Strong Watchlist Candidate",
            "Buy Only Above Confirmation Level",
            "Accumulate on Dips",
            "Short-Term Trade Candidate",
            "Long-Term Investment Candidate",
            "Avoid",
            "High Risk / Speculative",
            "Wait for Better Entry",
        }
        assert payload["sections"]["J. Data Quality Warning"]["live_market_confirmation_required"] is True

    def test_research_unknown_equity_symbol_is_rejected(self):
        with pytest.raises(ApiError) as exc:
            _research_payload({"asset_type": ["equity"], "symbol": ["NOTLISTED999"]})

        assert exc.value.status == HTTPStatus.BAD_REQUEST
        assert "supported NSE equity universe" in exc.value.message

    def test_research_validates_asset_type_and_horizon(self):
        with pytest.raises(ApiError) as asset_exc:
            _research_payload({"asset_type": ["crypto"], "symbol": ["BTCINR"]})
        with pytest.raises(ApiError) as horizon_exc:
            _research_payload({"asset_type": ["equity"], "symbol": ["RELIANCE"], "horizon": ["forever"]})

        assert asset_exc.value.status == HTTPStatus.BAD_REQUEST
        assert horizon_exc.value.status == HTTPStatus.BAD_REQUEST

    def test_research_mutual_fund_accepts_underscore_symbol(self):
        payload = _research_payload(
            {
                "asset_type": ["mutual_fund"],
                "symbol": ["PPFAS_FLEXI"],
                "horizon": ["long-term"],
            }
        )

        assert payload["asset_type"] == "mutual_fund"
        assert payload["symbol"] == "PPFAS_FLEXI"
        assert list(payload["sections"]) == EXPECTED_RESEARCH_SECTIONS

    def test_final_shortlist_returns_ranked_research_only_entries(self):
        payload = _final_shortlist_payload({"symbols": ["RELIANCE,TCS,INFY"], "limit": ["3"]})

        assert payload["title"] == "Final Shortlist"
        assert payload["research_only"] is True
        assert payload["auto_execution_enabled"] is False
        assert len(payload["entries"]) == 3
        first = payload["entries"][0]
        assert first["rank"] == 1
        assert first["symbol"] == "RELIANCE"
        assert first["action"] in {
            "Strong watchlist candidate",
            "Buy only above confirmation level",
            "Accumulate on dips",
            "Short-term trade candidate",
            "Long-term investment candidate",
            "Avoid",
            "High risk / speculative",
            "Wait for better entry",
            "Wait for pullback",
        }
        assert "reason" in first
        assert first["live_market_confirmation_required"] is True

    def test_final_shortlist_rejects_unknown_or_unsafe_symbols(self):
        with pytest.raises(ApiError) as unsafe_exc:
            _final_shortlist_payload({"symbols": ["<script>"]})
        with pytest.raises(ApiError) as unknown_exc:
            _final_shortlist_payload({"symbols": ["NOTLISTED999"]})

        assert unsafe_exc.value.status == HTTPStatus.BAD_REQUEST
        assert unknown_exc.value.status == HTTPStatus.BAD_REQUEST

    def test_options_review_accepts_covered_call(self):
        payload = _options_payload(
            {
                "underlying": ["RELIANCE"],
                "option_type": ["call"],
                "strike": ["3100"],
                "expiry_days": ["30"],
                "lot_size": ["250"],
                "last_price": ["42"],
                "implied_vol_pct": ["22"],
                "underlying_price": ["2950"],
                "holding_qty": ["250"],
                "holding_avg": ["2800"],
                "portfolio_value": ["3000000"],
                "requested_lots": ["1"],
            }
        )

        assert payload["research_only"] is True
        assert payload["hedge_review"]["status"] == "approved_for_review"
        assert payload["final_decision"]["category"] == "Manual Review Candidate"
        assert payload["payoff"]["available"] is True

    def test_options_review_blocks_naked_or_weekly_options(self):
        naked = _options_payload(
            {
                "underlying": ["RELIANCE"],
                "option_type": ["call"],
                "strike": ["3100"],
                "expiry_days": ["30"],
                "lot_size": ["250"],
                "last_price": ["42"],
                "implied_vol_pct": ["22"],
                "underlying_price": ["2950"],
                "holding_qty": ["0"],
            }
        )
        weekly = _options_payload(
            {
                "underlying": ["RELIANCE"],
                "option_type": ["call"],
                "strike": ["3100"],
                "expiry_days": ["5"],
                "lot_size": ["250"],
                "last_price": ["42"],
                "implied_vol_pct": ["22"],
                "underlying_price": ["2950"],
                "holding_qty": ["250"],
            }
        )

        assert naked["final_decision"]["category"] == "Blocked"
        assert weekly["final_decision"]["category"] == "Blocked"
