"""Trade Research Card builder and narration tests."""

from sentinel.trade_card.builder import ANALYSIS_ONLY_BANNER, TradeResearchCardBuilder
from sentinel.trade_card.llm_narration import (
    build_grounded_narration_prompt,
    validate_narration_grounding,
)
from sentinel.trade_card.narration import narrate_card
from sentinel.trade_card.news_extractor import extract_news_context, scrub_text


def _base_candidate(**extra):
    candidate = {
        "symbol": "RELIANCE",
        "screener": "s1_momentum",
        "sector": "Energy",
        "direction": "BUY",
        "conviction_score": 78,
        "entry_low": 2940,
        "entry_high": 2960,
        "stop_loss": 2880,
        "target_1": 3100,
        "target_2": 3200,
        "rr_ratio": 2.14,
        "suggested_qty": 10,
        "thesis": "Breakout with volume confirmation.",
        "risks": ["Market-wide reversal"],
    }
    candidate.update(extra)
    return candidate


class TestTradeResearchCard:

    def test_equity_candidate_builds_executable_card(self):
        card = TradeResearchCardBuilder().build(_base_candidate())
        assert card.is_executable
        assert card.amber_banner == ""
        assert card.gross_position_value_inr == 29_500
        assert card.risk_amount_inr == 700
        assert card.estimated_round_trip_cost_inr > 0
        assert card.feature_breakdown["sector"] == "Energy"

    def test_analysis_only_candidate_has_mandatory_amber_banner(self):
        card = TradeResearchCardBuilder().build(_base_candidate(
            symbol="EURUSD",
            screener="s7_forex",
            amber_banner=True,
            execution_eligible=False,
            entry_low=1.08,
            entry_high=1.082,
            stop_loss=1.07,
            target_1=1.105,
            suggested_qty=1,
        ))
        assert not card.is_executable
        assert card.amber_banner == ANALYSIS_ONLY_BANNER
        assert any("analysis-only" in warning for warning in card.warnings)
        assert card.estimated_round_trip_cost_inr == 0.0

    def test_narration_restates_structured_fields_only(self):
        card = TradeResearchCardBuilder().build(_base_candidate())
        text = narrate_card(card)
        assert "RELIANCE" in text
        assert "78/100" in text
        assert "1:2.14" in text
        assert "Estimated round-trip cost" in text

    def test_narration_includes_analysis_only_banner(self):
        card = TradeResearchCardBuilder().build(_base_candidate(
            symbol="XAUUSD",
            amber_banner=True,
            execution_eligible=False,
        ))
        assert ANALYSIS_ONLY_BANNER in narrate_card(card)

    def test_news_context_scrubs_pii_and_limits_items(self):
        context = extract_news_context("RELIANCE", [
            {
                "title": "Reliance update from analyst test@example.com +91 98765 43210",
                "source": {"name": "Newswire"},
                "publishedAt": "2026-05-24T09:00:00Z",
            },
            {
                "title": "Second update",
                "source": {"name": "Exchange"},
                "publishedAt": "2026-05-24T10:00:00Z",
            },
        ], max_items=1)
        assert len(context.items) == 1
        assert "[email]" in context.items[0].title
        assert "[phone]" in context.items[0].title
        assert scrub_text("hello   world") == "hello world"

    def test_grounded_prompt_contains_fields_and_forbids_signal_generation(self):
        card = TradeResearchCardBuilder().build(_base_candidate())
        context = extract_news_context("RELIANCE", [{
            "title": "Company files exchange update",
            "source": {"name": "Exchange"},
            "publishedAt": "2026-05-24T09:00:00Z",
        }])
        prompt = build_grounded_narration_prompt(card, context)
        assert "must not originate signals" in prompt.system
        assert "RELIANCE" in prompt.user
        assert "Company files exchange update" in prompt.user

    def test_grounding_validator_flags_prediction_language(self):
        violations = validate_narration_grounding("This will rise and is guaranteed.")
        assert "will rise" in violations
        assert "guaranteed" in violations
