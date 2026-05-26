from http import HTTPStatus

import pytest

from sentinel.ui.react_api import (
    ApiError,
    _equity_symbols,
    _fundamentals_payload,
    _mf_payload,
    _validated_symbol,
)


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
