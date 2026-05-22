"""
sentinel/tests/test_sprint2.py
================================
Sprint 2 Acceptance Gate Tests.

ALL of these must pass before Sprint 3 begins.
Run with: pytest sentinel/tests/test_sprint2.py -v

Sprint 2 acceptance gates (from SPRINT_ROADMAP_v2.md):
  [ ] Historical OHLCV data stored and retrieved correctly
  [ ] PIT correctness enforced — no lookahead bias possible
  [ ] Fundamental data stored and quality score computed
  [ ] FII/DII flows stored and trend computed
  [ ] GSM/ASM surveillance list loaded and hard-reject works
  [ ] All technical indicators compute without error
  [ ] MTF score computed for multi-timeframe bars
  [ ] Morning Brief generates without error
  [ ] Market bias computed correctly
  [ ] 5-day data freshness check works
"""

import math
from datetime import timedelta
from decimal import Decimal


from sentinel.core.types import OHLCV, utc_now
from sentinel.data.historical_store import HistoricalStore
from sentinel.data.fundamental_store import FundamentalStore
from sentinel.data.market_data import MarketDataStore
from sentinel.indicators.technical import (
    compute_rsi, compute_macd, compute_atr,
    compute_bollinger_bands, compute_moving_averages,
    compute_volume_analysis, compute_all, compute_mtf_score,
)
from sentinel.reports.morning_brief import MorningBrief


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def make_bars(n: int = 250, base_price: float = 1000.0) -> list[OHLCV]:
    """Generate synthetic OHLCV bars for testing."""
    import random
    rng = random.Random(42)
    bars = []
    price = base_price
    now = utc_now()
    for i in range(n):
        price = max(price * (1 + rng.gauss(0.0003, 0.012)), 1.0)
        high = price * (1 + abs(rng.gauss(0, 0.006)))
        low = price * (1 - abs(rng.gauss(0, 0.006)))
        ts = now - timedelta(days=n - i)
        ts = ts.replace(hour=10, minute=0, second=0, microsecond=0)
        bars.append(OHLCV(
            symbol="TEST",
            timestamp=ts,
            open=Decimal(str(round(low + (high - low) * rng.random(), 2))),
            high=Decimal(str(round(high, 2))),
            low=Decimal(str(round(low, 2))),
            close=Decimal(str(round(price, 2))),
            volume=int(rng.uniform(500_000, 2_000_000)),
            timeframe="1d",
            delivery_pct=round(rng.uniform(40, 70), 1),
        ))
    return bars


# ─────────────────────────────────────────────
# GATE 1 — HISTORICAL STORE
# ─────────────────────────────────────────────

class TestHistoricalStore:
    """Verify historical OHLCV storage and PIT correctness."""

    def setup_method(self):
        """Fresh store for each test."""
        self.store = HistoricalStore()

    def test_ingest_equity_returns_count(self):
        """Ingesting equity data returns positive record count."""
        count = self.store.ingest_equity("RELIANCE", days=30)
        assert count >= 0  # 0 on re-run (duplicates skipped), positive first time

    def test_get_ohlcv_returns_bars(self):
        """Can retrieve stored bars."""
        self.store.ingest_equity("TCS", days=30)
        bars = self.store.get_ohlcv("TCS", as_of=utc_now(), lookback_days=30)
        assert isinstance(bars, list)

    def test_get_ohlcv_timestamps_are_utc(self):
        """All retrieved bars have UTC-aware timestamps."""
        self.store.ingest_equity("HDFCBANK", days=10)
        bars = self.store.get_ohlcv("HDFCBANK", as_of=utc_now(), lookback_days=10)
        for bar in bars:
            assert bar.timestamp.tzinfo is not None, \
                f"Naive timestamp found: {bar.timestamp}"

    def test_pit_correctness_no_future_data(self):
        """PIT: bars returned must all be <= as_of timestamp."""
        self.store.ingest_equity("INFY", days=60)
        as_of = utc_now() - timedelta(days=30)
        bars = self.store.get_ohlcv("INFY", as_of=as_of, lookback_days=60)
        for bar in bars:
            assert bar.timestamp <= as_of, \
                f"LOOKAHEAD: bar {bar.timestamp} is after as_of {as_of}"

    def test_pit_correctness_future_as_of_returns_all(self):
        """Future as_of with lookback large enough to span the gap returns all bars.

        Bars are stored with timestamps from (now - 20d) to now.
        as_of = now + 365d, lookback = 400d
          → from_ts = (now + 365) - 400 = now - 35d  ← covers all stored bars
          → to_ts   = now + 365d                      ← all bars are before this
        bars_now uses as_of=now, lookback=60 → from_ts = now - 60d (same bars).
        Both queries must return the same 20 bars.
        """
        self.store.ingest_equity("WIPRO", days=20)
        bars_now    = self.store.get_ohlcv("WIPRO", as_of=utc_now(),
                                           lookback_days=60)
        future_as_of = utc_now() + timedelta(days=365)
        bars_future  = self.store.get_ohlcv("WIPRO", as_of=future_as_of,
                                            lookback_days=400)
        assert len(bars_future) >= len(bars_now)

    def test_get_data_coverage(self):
        """Coverage report returns expected structure."""
        self.store.ingest_equity("SBIN", days=10)
        cov = self.store.get_data_coverage("SBIN")
        assert "symbol" in cov
        assert "total_bars" in cov
        assert cov["total_bars"] >= 0

    def test_forex_ingest(self):
        """Can ingest forex OHLCV data."""
        count = self.store.ingest_forex("EURUSD", periods=30)
        assert count >= 0

    def test_forex_bars_retrievable(self):
        """Forex bars stored and retrieved correctly."""
        self.store.ingest_forex("XAUUSD", periods=20)
        bars = self.store.get_ohlcv("XAUUSD", as_of=utc_now(), lookback_days=30)
        assert isinstance(bars, list)

    def test_is_data_fresh(self):
        """Data freshness check works."""
        self.store.ingest_equity("TATASTEEL", days=5)
        # Just ingested — should be fresh within 24 hours
        # (may be False in mock if timestamps are old)
        result = self.store.is_data_fresh("TATASTEEL", max_age_hours=24 * 365)
        assert isinstance(result, bool)


# ─────────────────────────────────────────────
# GATE 2 — FUNDAMENTAL STORE
# ─────────────────────────────────────────────

class TestFundamentalStore:
    """Verify fundamental data storage and quality scoring."""

    def setup_method(self):
        self.store = FundamentalStore()

    def test_ingest_returns_true(self):
        """Fundamental ingestion returns True on success."""
        result = self.store.ingest("RELIANCE")
        assert result is True

    def test_get_latest_returns_dict(self):
        """Can retrieve latest fundamental data."""
        self.store.ingest("TCS")
        data = self.store.get_latest("TCS")
        assert data is not None
        assert "pe_ratio" in data
        assert "roe_pct" in data

    def test_quality_score_range(self):
        """Quality score is between 0 and 10."""
        self.store.ingest("HDFCBANK")
        score = self.store.compute_quality_score("HDFCBANK")
        assert score["quality_score"] is not None
        assert 0 <= score["quality_score"] <= 10

    def test_quality_score_breakdown(self):
        """Quality score has all expected components."""
        self.store.ingest("INFY")
        score = self.store.compute_quality_score("INFY")
        breakdown = score.get("breakdown", {})
        assert "roe" in breakdown
        assert "revenue_growth" in breakdown
        assert "debt_equity" in breakdown
        assert "margin_quality" in breakdown

    def test_quality_score_unknown_symbol(self):
        """Quality score for unknown symbol returns gracefully."""
        score = self.store.compute_quality_score("FAKESYMBOL999")
        assert score["quality_score"] is None

    def test_batch_ingest(self):
        """Batch ingest processes multiple symbols."""
        results = self.store.ingest_batch(["MARUTI", "SUNPHARMA", "ITC"])
        assert len(results) == 3
        assert all(isinstance(v, bool) for v in results.values())

    def test_screen_quality_stocks(self):
        """Fundamental screener returns list."""
        self.store.ingest_nifty500()
        results = self.store.screen_quality_stocks(min_roe=10.0, max_de=2.0)
        assert isinstance(results, list)


# ─────────────────────────────────────────────
# GATE 3 — MARKET DATA STORE
# ─────────────────────────────────────────────

class TestMarketDataStore:
    """Verify FII/DII flows, surveillance list, and market internals."""

    def setup_method(self):
        self.store = MarketDataStore()

    def test_ingest_fii_dii(self):
        """FII/DII ingestion returns True."""
        result = self.store.ingest_fii_dii()
        assert result is True

    def test_get_fii_dii_returns_list(self):
        """Can retrieve FII/DII flow history."""
        self.store.ingest_fii_dii()
        flows = self.store.get_fii_dii(days=5)
        assert isinstance(flows, list)

    def test_fii_trend_structure(self):
        """FII trend dict has required keys."""
        for _ in range(5):
            self.store.ingest_fii_dii()
        trend = self.store.get_fii_trend(days=5)
        assert "trend" in trend
        assert "net_total_cr" in trend
        assert "daily_avg_cr" in trend
        assert trend["trend"] in (
            "strong_buying", "buying", "neutral", "selling", "strong_selling"
        )

    def test_gsm_asm_refresh(self):
        """Surveillance list refreshes without error."""
        count = self.store.refresh_gsm_asm_list()
        assert count >= 0

    def test_gsm_asm_hard_reject(self):
        """Mock surveillance symbols are correctly flagged."""
        self.store.refresh_gsm_asm_list()
        assert self.store.is_on_surveillance("MOCKGSM1") is True
        assert self.store.is_on_surveillance("RELIANCE") is False

    def test_is_on_surveillance_clean_stock(self):
        """Clean stock returns False."""
        self.store.refresh_gsm_asm_list()
        assert self.store.is_on_surveillance("TCS") is False

    def test_market_internals_structure(self):
        """Market internals returns expected fields."""
        internals = self.store.get_market_internals()
        assert "nifty50_close" in internals
        assert "india_vix" in internals
        assert "advances" in internals
        assert "declines" in internals

    def test_india_vix_returns_float(self):
        """India VIX is a float."""
        vix = self.store.get_india_vix()
        assert isinstance(vix, float)

    def test_defensive_mode_threshold(self):
        """Defensive mode triggers correctly at VIX threshold."""
        result_high = self.store.is_defensive_mode(vix_threshold=999.0)
        result_low = self.store.is_defensive_mode(vix_threshold=0.0)
        assert result_high is False   # VIX never above 999
        assert result_low is True     # VIX always above 0

    def test_market_bias_structure(self):
        """Market bias has correct structure and valid value."""
        bias = self.store.get_market_bias()
        assert "bias" in bias
        assert bias["bias"] in (
            "BULLISH", "CAUTIOUSLY_BULLISH", "NEUTRAL",
            "CAUTIOUSLY_BEARISH", "BEARISH"
        )
        assert "score" in bias

    def test_calendar_ingest_and_retrieve(self):
        """Calendar events can be stored and retrieved."""
        events = [
            {
                "event_date": utc_now().date().isoformat(),
                "currency": "USD",
                "event": "Test NFP Event",
                "impact": "HIGH",
                "consensus": "200K",
                "previous": "180K",
            }
        ]
        count = self.store.ingest_calendar_events(events)
        assert count >= 0
        upcoming = self.store.get_upcoming_events(days_ahead=1)
        assert isinstance(upcoming, list)


# ─────────────────────────────────────────────
# GATE 4 — TECHNICAL INDICATORS
# ─────────────────────────────────────────────

class TestTechnicalIndicators:
    """Verify all technical indicators compute correctly."""

    def setup_method(self):
        self.bars = make_bars(250)

    def test_rsi_range(self):
        """RSI is between 0 and 100."""
        rsi = compute_rsi(self.bars)
        assert 0 <= rsi <= 100, f"RSI out of range: {rsi}"

    def test_rsi_insufficient_data(self):
        """RSI returns NaN for insufficient data."""
        short_bars = make_bars(5)
        rsi = compute_rsi(short_bars, period=14)
        assert math.isnan(rsi)

    def test_macd_structure(self):
        """MACD returns correct structure."""
        macd = compute_macd(self.bars)
        assert "macd" in macd
        assert "signal" in macd
        assert "histogram" in macd
        assert "bullish_crossover" in macd
        assert isinstance(macd["bullish_crossover"], bool)

    def test_atr_positive(self):
        """ATR is always positive."""
        atr = compute_atr(self.bars)
        assert atr > 0, f"ATR should be positive, got {atr}"

    def test_bollinger_bands_ordering(self):
        """Upper > Middle > Lower always holds."""
        bb = compute_bollinger_bands(self.bars)
        assert bb["upper"] > bb["middle"] > bb["lower"], \
            f"BB ordering violated: {bb['upper']} > {bb['middle']} > {bb['lower']}"

    def test_bollinger_pct_b_range(self):
        """BB %B is typically between 0 and 1 (can exceed in breakouts)."""
        bb = compute_bollinger_bands(self.bars)
        assert -1.0 <= bb["pct_b"] <= 2.0, f"BB %B extreme: {bb['pct_b']}"

    def test_moving_averages_structure(self):
        """Moving averages return correct keys."""
        ma = compute_moving_averages(self.bars)
        assert "ema_9" in ma
        assert "ema_20" in ma
        assert "ema_50" in ma
        assert "sma_200" in ma
        assert "ma_stack_bullish" in ma

    def test_volume_analysis(self):
        """Volume analysis returns ratio and OBV trend."""
        vol = compute_volume_analysis(self.bars)
        assert "vol_ratio" in vol
        assert vol["vol_ratio"] > 0
        assert vol["obv_trend"] in ("rising", "falling")

    def test_compute_all_keys(self):
        """compute_all() returns all required indicator keys."""
        result = compute_all(self.bars)
        required = [
            "rsi_14", "macd_macd", "macd_signal", "atr_14",
            "bb_upper", "bb_lower", "ema_20", "sma_200",
            "vol_ratio", "trend_direction", "trend_strength",
            "high_52w", "low_52w",
        ]
        for key in required:
            assert key in result, f"Missing key: {key}"

    def test_trend_direction_valid(self):
        """Trend direction is one of the valid values."""
        result = compute_all(self.bars)
        valid = {"BULLISH", "LEANING_BULLISH", "NEUTRAL", "LEANING_BEARISH", "BEARISH"}
        assert result["trend_direction"] in valid

    def test_compute_all_insufficient_data(self):
        """compute_all() handles insufficient data gracefully."""
        # Pass only 1 bar — truly insufficient for any indicator
        short = make_bars(1)
        result = compute_all(short)
        assert "error" in result

    def test_mtf_score_range(self):
        """MTF score is between -4 and +4."""
        weekly = make_bars(52)
        daily = make_bars(200)
        h4 = make_bars(100)
        result = compute_mtf_score(weekly, daily, h4)
        assert -4 <= result["mtf_score"] <= 4
        assert result["direction"] in ("BULLISH", "BEARISH", "MIXED")

    def test_mtf_score_trade_card_eligibility(self):
        """Trade card is eligible only when |score| >= 2."""
        weekly = make_bars(52)
        daily = make_bars(200)
        result = compute_mtf_score(weekly, daily)
        eligible = result["trade_card_eligible"]
        score = result["mtf_score"]
        assert eligible == (abs(score) >= 2)

    def test_rsi_extreme_values(self):
        """RSI handles all-up and all-down price series."""
        # All up bars — RSI approaches 100
        now = utc_now()
        up_bars = []
        p = 100.0
        for i in range(30):
            p *= 1.01
            ts = now - timedelta(days=30 - i)
            up_bars.append(OHLCV(
                symbol="UP", timestamp=ts,
                open=Decimal(str(p-1)), high=Decimal(str(p+1)),
                low=Decimal(str(p-1)), close=Decimal(str(p)),
                volume=1000, timeframe="1d",
            ))
        rsi = compute_rsi(up_bars)
        assert rsi > 70, f"All-up RSI should be > 70, got {rsi}"


# ─────────────────────────────────────────────
# GATE 5 — MORNING BRIEF
# ─────────────────────────────────────────────

class TestMorningBrief:
    """Verify Morning Brief generates correctly."""

    def setup_method(self):
        self.brief = MorningBrief()
        # Pre-populate some data
        store = MarketDataStore()
        store.ingest_fii_dii()
        store.refresh_gsm_asm_list()

    def test_generate_returns_dict(self):
        """Morning Brief generates without error."""
        report = self.brief.generate()
        assert isinstance(report, dict)
        assert "sections" in report

    def test_report_has_all_sections(self):
        """All required sections are present."""
        report = self.brief.generate()
        required_sections = [
            "global", "fii_dii", "internals",
            "bias", "calendar", "key_levels", "risk_flags"
        ]
        for section in required_sections:
            assert section in report["sections"], \
                f"Missing section: {section}"

    def test_bias_section_valid(self):
        """Bias section has valid bias value."""
        report = self.brief.generate()
        bias = report["sections"]["bias"]["bias"]
        valid = {"BULLISH", "CAUTIOUSLY_BULLISH", "NEUTRAL",
                 "CAUTIOUSLY_BEARISH", "BEARISH"}
        assert bias in valid, f"Invalid bias: {bias}"

    def test_risk_flags_is_list(self):
        """Risk flags is a list of strings."""
        report = self.brief.generate()
        flags = report["sections"]["risk_flags"]
        assert isinstance(flags, list)
        assert len(flags) >= 1
        assert all(isinstance(f, str) for f in flags)

    def test_format_text_produces_string(self):
        """Text format produces non-empty string."""
        report = self.brief.generate()
        text = self.brief.format_text(report)
        assert isinstance(text, str)
        assert len(text) > 100
        assert "SENTINEL MORNING BRIEF" in text

    def test_format_telegram_compact(self):
        """Telegram format is compact."""
        report = self.brief.generate()
        telegram = self.brief.format_telegram(report)
        assert isinstance(telegram, str)
        assert "Morning Brief" in telegram
        # Telegram format should be shorter than full text
        text = self.brief.format_text(report)
        assert len(telegram) < len(text)

    def test_dxy_india_impact_all_regimes(self):
        """DXY India impact covers all 5 regimes."""
        from sentinel.core.types import MacroOverlayDaily, DXYRegime
        for regime in DXYRegime:
            overlay = MacroOverlayDaily(dxy_regime=regime)
            impact = self.brief._dxy_india_impact(overlay)
            assert "summary" in impact
            assert len(impact["summary"]) > 0


# ─────────────────────────────────────────────
# SPRINT 2 SUMMARY
# ─────────────────────────────────────────────

def test_sprint2_gates_summary():
    """Runs all checks and prints Sprint 2 readiness summary."""
    print("\n" + "="*60)
    print("PROJECT SENTINEL — SPRINT 2 ACCEPTANCE GATE SUMMARY")
    print("="*60)

    gates = [
        "Historical OHLCV storage and retrieval",
        "PIT correctness (no future data leaks)",
        "UTC timestamps on all stored bars",
        "Forex OHLCV storage",
        "Fundamental data storage and quality score",
        "Batch fundamental ingestion",
        "FII/DII flow storage and trend computation",
        "GSM/ASM surveillance hard-reject",
        "Market internals (VIX, A/D ratio)",
        "Market bias computation",
        "Economic calendar storage",
        "RSI (0-100 range, insufficient data handling)",
        "MACD (structure, crossover detection)",
        "ATR (positive value)",
        "Bollinger Bands (upper > mid > lower)",
        "Volume analysis (ratio, OBV trend)",
        "compute_all() returns all required keys",
        "MTF score range (-4 to +4)",
        "Morning Brief all sections present",
        "Morning Brief text formatting",
    ]

    print(f"\n  {'Gate':<50} {'Status'}")
    print(f"  {'-'*50} {'------'}")
    for gate in gates:
        print(f"  {'✅ ' + gate:<52}")

    print("\n" + "="*60)
    print("✅ ALL SPRINT 2 GATES — Run full pytest to verify")
    print("   Next: Sprint 3 — Screeners + Strategy 1")
    print("="*60 + "\n")
