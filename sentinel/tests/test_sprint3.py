"""
sentinel/tests/test_sprint3.py
================================
Sprint 3 Acceptance Gate Tests.

ALL must pass before Sprint 4 begins.
Run with: pytest sentinel/tests/test_sprint3.py -v

Sprint 3 gates:
  [ ] All 7 screeners run without error
  [ ] Each screener returns correct structure
  [ ] GSM/ASM hard rejection works in screeners
  [ ] S4 result cap enforced (max 5 always)
  [ ] S7 forex amber banner present on non-INR pairs
  [ ] Conviction score within 0-100 for all results
  [ ] R:R >= 2.0 for all results
  [ ] ScreenerRunner.run_all() returns all 7 keys
  [ ] Morning brief text formatting has no TypeError
  [ ] Dashboard imports succeed (no crash on import)
"""

import pytest

from sentinel.screeners.runner import ScreenerRunner
from sentinel.reports.morning_brief import MorningBrief
from sentinel.data.market_data import MarketDataStore


# ─────────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────────

@pytest.fixture(scope="module")
def runner():
    return ScreenerRunner()

@pytest.fixture(scope="module")
def all_results(runner):
    return runner.run_all()


# ─────────────────────────────────────────────
# GATE 1 — SCREENER RUNNER
# ─────────────────────────────────────────────

class TestScreenerRunner:

    def test_run_all_returns_all_keys(self, all_results):
        expected = {
            "s1_momentum", "s2_value", "s3_sector",
            "s4_penny", "s5_institutional", "s6_mf", "s7_forex",
        }
        for key in expected:
            assert key in all_results, f"Missing screener key: {key}"

    def test_run_all_has_summary(self, all_results):
        assert "_summary" in all_results
        summary = all_results["_summary"]
        assert "total_candidates" in summary
        assert "screeners_run" in summary
        assert summary["screeners_run"] == 7

    def test_run_one_returns_result(self, runner):
        result = runner.run_one("s1_momentum")
        assert "candidates" in result
        assert "meta" in result

    def test_run_one_unknown_screener(self, runner):
        result = runner.run_one("s99_nonexistent")
        assert result.get("error") is not None
        assert result["candidates"] == []


# ─────────────────────────────────────────────
# GATE 2 — RESULT STRUCTURE VALIDATION
# ─────────────────────────────────────────────

class TestResultStructure:
    """Every screener result must have correct structure."""

    REQUIRED_CANDIDATE_KEYS = [
        "symbol", "sector", "direction", "conviction_score",
        "entry_low", "entry_high", "stop_loss", "target_1",
        "rr_ratio", "suggested_qty", "thesis", "risks",
    ]

    def _validate_screener(self, results, key):
        result = results.get(key, {})
        assert "candidates" in result, f"{key}: missing 'candidates'"
        assert "meta" in result,       f"{key}: missing 'meta'"
        assert "screener" in result,   f"{key}: missing 'screener'"

        for cand in result["candidates"]:
            for k in self.REQUIRED_CANDIDATE_KEYS:
                assert k in cand, f"{key}: candidate missing '{k}'"

    def test_s1_structure(self, all_results):
        self._validate_screener(all_results, "s1_momentum")

    def test_s2_structure(self, all_results):
        self._validate_screener(all_results, "s2_value")

    def test_s3_structure(self, all_results):
        self._validate_screener(all_results, "s3_sector")

    def test_s4_structure(self, all_results):
        self._validate_screener(all_results, "s4_penny")

    def test_s5_structure(self, all_results):
        self._validate_screener(all_results, "s5_institutional")

    def test_s6_structure(self, all_results):
        self._validate_screener(all_results, "s6_mf")

    def test_s7_structure(self, all_results):
        self._validate_screener(all_results, "s7_forex")


# ─────────────────────────────────────────────
# GATE 3 — CONVICTION SCORES AND R:R
# ─────────────────────────────────────────────

class TestQualityRules:

    def test_conviction_scores_in_range(self, all_results):
        """All conviction scores are between 0 and 100."""
        for key, result in all_results.items():
            if key.startswith("_"):
                continue
            for cand in result.get("candidates", []):
                score = cand.get("conviction_score", 0)
                assert 0 <= score <= 100, \
                    f"{key}/{cand['symbol']}: score {score} out of range"

    def test_rr_ratio_minimum(self, all_results):
        """All R:R ratios are >= 2.0."""
        for key, result in all_results.items():
            if key.startswith("_"):
                continue
            for cand in result.get("candidates", []):
                rr = cand.get("rr_ratio", 0)
                assert rr >= 2.0, \
                    f"{key}/{cand['symbol']}: R:R {rr} below minimum 2.0"

    def test_entry_zone_valid(self, all_results):
        """entry_high >= entry_low for all candidates."""
        for key, result in all_results.items():
            if key.startswith("_"):
                continue
            for cand in result.get("candidates", []):
                el = cand.get("entry_low", 0)
                eh = cand.get("entry_high", 0)
                assert eh >= el, \
                    f"{key}/{cand['symbol']}: entry_high {eh} < entry_low {el}"

    def test_direction_valid(self, all_results):
        """Direction is BUY or SELL."""
        for key, result in all_results.items():
            if key.startswith("_"):
                continue
            for cand in result.get("candidates", []):
                d = cand.get("direction", "")
                assert d in ("BUY", "SELL"), \
                    f"{key}/{cand['symbol']}: invalid direction '{d}'"

    def test_thesis_not_empty(self, all_results):
        """All candidates have non-empty thesis."""
        for key, result in all_results.items():
            if key.startswith("_"):
                continue
            for cand in result.get("candidates", []):
                assert cand.get("thesis"), \
                    f"{key}/{cand['symbol']}: empty thesis"

    def test_risks_is_list(self, all_results):
        """Risks is a list."""
        for key, result in all_results.items():
            if key.startswith("_"):
                continue
            for cand in result.get("candidates", []):
                assert isinstance(cand.get("risks"), list), \
                    f"{key}/{cand['symbol']}: risks must be a list"


# ─────────────────────────────────────────────
# GATE 4 — S4 HARD CAP
# ─────────────────────────────────────────────

class TestS4HardCap:

    def test_s4_max_5_results(self, all_results):
        """S4 must never return more than 5 results — hard cap."""
        s4 = all_results.get("s4_penny", {})
        count = len(s4.get("candidates", []))
        assert count <= 5, \
            f"S4 returned {count} results — hard cap is 5, non-negotiable"

    def test_s4_has_high_risk_flag(self, all_results):
        """S4 candidates must have high_risk=True."""
        s4 = all_results.get("s4_penny", {})
        for cand in s4.get("candidates", []):
            assert cand.get("high_risk") is True, \
                f"S4 candidate {cand['symbol']} missing high_risk=True"

    def test_s4_max_allocation_pct(self, all_results):
        """S4 candidates must have max_allocation_pct=0.5."""
        s4 = all_results.get("s4_penny", {})
        for cand in s4.get("candidates", []):
            assert cand.get("max_allocation_pct") == 0.5, \
                f"S4 {cand['symbol']}: max_allocation_pct must be 0.5"


# ─────────────────────────────────────────────
# GATE 5 — S7 FOREX AMBER BANNER
# ─────────────────────────────────────────────

class TestS7ForexAmberBanner:

    def test_non_inr_pairs_have_amber_banner(self, all_results):
        """Non-INR forex pairs must have amber_banner=True."""
        s7 = all_results.get("s7_forex", {})
        for cand in s7.get("candidates", []):
            symbol = cand.get("symbol", "")
            if not symbol.endswith("INR"):
                assert cand.get("amber_banner") is True, \
                    f"S7 {symbol}: non-INR pair must have amber_banner=True"

    def test_inr_pairs_execution_eligible(self, all_results):
        """INR pairs must be marked execution_eligible=True."""
        s7 = all_results.get("s7_forex", {})
        for cand in s7.get("candidates", []):
            if cand.get("symbol", "").endswith("INR"):
                assert cand.get("execution_eligible") is True, \
                    f"S7 {cand['symbol']}: INR pair should be execution_eligible"

    def test_s7_has_cot_data(self, all_results):
        """S7 candidates must have COT index."""
        s7 = all_results.get("s7_forex", {})
        for cand in s7.get("candidates", []):
            assert "cot_index" in cand, \
                f"S7 {cand['symbol']}: missing cot_index"
            assert 0 <= cand["cot_index"] <= 100


# ─────────────────────────────────────────────
# GATE 6 — GSM/ASM HARD REJECTION IN SCREENERS
# ─────────────────────────────────────────────

class TestGSMASMRejection:

    def test_gsm_symbols_not_in_results(self, all_results):
        """Mock GSM symbols must not appear in any screener results."""
        mkt = MarketDataStore()
        mkt.refresh_gsm_asm_list()
        surveillance = set(mkt.get_surveillance_list())

        for key, result in all_results.items():
            if key.startswith("_"):
                continue
            for cand in result.get("candidates", []):
                symbol = cand.get("symbol", "")
                assert symbol not in surveillance, \
                    f"{key}: GSM/ASM symbol '{symbol}' appeared in results"

    def test_gsm_rejection_count_in_meta(self, all_results):
        """Meta should report how many were GSM-rejected."""
        for key, result in all_results.items():
            if key.startswith("_") or not result.get("meta"):
                continue
            # gsm_rejected key must exist (can be 0)
            assert "gsm_rejected" in result["meta"], \
                f"{key}: meta missing 'gsm_rejected' count"


# ─────────────────────────────────────────────
# GATE 7 — MORNING BRIEF NO TYPEERROR
# ─────────────────────────────────────────────

class TestMorningBriefFormatting:
    """Verify all None values are safely handled in format_text."""

    def test_format_text_no_type_error(self):
        """format_text must not raise TypeError on any None values."""
        from sentinel.data.market_data import MarketDataStore
        mkt = MarketDataStore()
        mkt.ingest_fii_dii()
        mkt.refresh_gsm_asm_list()

        brief = MorningBrief()
        report = brief.generate()

        # This must not raise TypeError
        try:
            text = brief.format_text(report)
            assert isinstance(text, str)
            assert len(text) > 50
        except TypeError as e:
            pytest.fail(
                f"format_text raised TypeError: {e}\n"
                "A None value is being formatted with a numeric format string."
            )

    def test_format_telegram_no_type_error(self):
        """format_telegram must not raise TypeError."""
        brief = MorningBrief()
        report = brief.generate()
        try:
            msg = brief.format_telegram(report)
            assert isinstance(msg, str)
        except TypeError as e:
            pytest.fail(f"format_telegram raised TypeError: {e}")

    def test_all_sections_present(self):
        """Morning brief has all required sections."""
        brief = MorningBrief()
        report = brief.generate()
        for section in ["global","fii_dii","internals","bias",
                        "calendar","key_levels","risk_flags"]:
            assert section in report["sections"], \
                f"Missing section: {section}"


# ─────────────────────────────────────────────
# SPRINT 3 SUMMARY
# ─────────────────────────────────────────────

def test_sprint3_gates_summary():
    """Print Sprint 3 readiness summary."""
    print("\n" + "="*60)
    print("PROJECT SENTINEL — SPRINT 3 ACCEPTANCE GATE SUMMARY")
    print("="*60)
    gates = [
        "ScreenerRunner.run_all() returns all 7 screener keys",
        "run_one() works for valid and invalid screener names",
        "All candidates have required structure keys",
        "Conviction scores 0-100 for all results",
        "R:R >= 2.0 enforced for all results",
        "Entry zone valid (high >= low) for all results",
        "Direction is BUY or SELL only",
        "Thesis non-empty for all candidates",
        "S4 hard cap: max 5 results always",
        "S4 high_risk=True on all candidates",
        "S4 max_allocation_pct=0.5 enforced",
        "S7 non-INR pairs have amber_banner=True",
        "S7 INR pairs have execution_eligible=True",
        "S7 all candidates have cot_index 0-100",
        "GSM/ASM symbols rejected from all screener results",
        "Meta includes gsm_rejected count",
        "Morning Brief format_text: no TypeError",
        "Morning Brief format_telegram: no TypeError",
        "Morning Brief: all 7 sections present",
    ]
    for g in gates:
        print(f"  ✅ {g}")
    print("\n" + "="*60)
    print("✅ ALL SPRINT 3 GATES — Run full pytest to verify")
    print("   Next: Sprint 4 — Behavioral Guardrails + MF Advisory")
    print("="*60 + "\n")
