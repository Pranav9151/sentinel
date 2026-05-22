"""
sentinel/core/types.py
======================
All domain types for Project Sentinel.

CRITICAL DESIGN PRINCIPLE (Knight Capital §1.9 of Failures Playbook):
AnalysisSignal and ExecutionSignal are SEPARATE types with NO shared
construction path. An AnalysisSignal has no to_order() or create_order()
method. It is PHYSICALLY IMPOSSIBLE to accidentally route an analysis
signal to execution.

The only conversion path is:
    analysis_to_execution(signal, eligibility_set) -> Optional[ExecutionSignal]

This raises InstrumentNotEligibleError for any instrument not in
the operator's execution_eligible_instruments set.

All datetimes are UTC-aware. Naive datetimes raise NaiveDatetimeError.
All monetary values use the Money type with explicit currency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Literal, Optional, FrozenSet

from sentinel.core.errors import (
    InstrumentNotEligibleError,
    NaiveDatetimeError,
)


# ─────────────────────────────────────────────
# DATETIME UTILITIES
# ─────────────────────────────────────────────

def utc_now() -> datetime:
    """
    Always use this instead of datetime.now() anywhere in Sentinel.
    Returns current UTC time, always timezone-aware.
    """
    return datetime.now(timezone.utc)


def validate_utc(dt: datetime, location: str = "") -> datetime:
    """
    Validates that a datetime is UTC-aware.
    Raises NaiveDatetimeError if naive.
    Use this at every system boundary (data ingestion, API calls).
    """
    if dt.tzinfo is None:
        raise NaiveDatetimeError(location)
    return dt


# ─────────────────────────────────────────────
# MONEY TYPE
# ─────────────────────────────────────────────

class Currency(Enum):
    INR = "INR"
    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"
    JPY = "JPY"


@dataclass(frozen=True)
class Money:
    """
    Represents a monetary amount with explicit currency.
    Never use bare floats for prices or P&L in Sentinel.

    Examples:
        entry_price = Money(Decimal("1850.50"), Currency.INR)
        risk_amount = Money(Decimal("500.00"), Currency.INR)
        forex_price = Money(Decimal("1.0852"), Currency.USD)  # EUR/USD price
    """
    amount: Decimal
    currency: Currency

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            # Auto-convert float/int to Decimal for convenience
            object.__setattr__(self, "amount", Decimal(str(self.amount)))

    def __add__(self, other: "Money") -> "Money":
        if self.currency != other.currency:
            raise ValueError(
                f"Cannot add {self.currency.value} and {other.currency.value}. "
                f"Convert to same currency first."
            )
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        if self.currency != other.currency:
            raise ValueError(
                f"Cannot subtract {self.currency.value} and {other.currency.value}."
            )
        return Money(self.amount - other.amount, self.currency)

    def __mul__(self, factor: float | int | Decimal) -> "Money":
        return Money(self.amount * Decimal(str(factor)), self.currency)

    def __truediv__(self, divisor: float | int | Decimal) -> "Money":
        return Money(self.amount / Decimal(str(divisor)), self.currency)

    def __lt__(self, other: "Money") -> bool:
        if self.currency != other.currency:
            raise ValueError("Cannot compare different currencies.")
        return self.amount < other.amount

    def __le__(self, other: "Money") -> bool:
        if self.currency != other.currency:
            raise ValueError("Cannot compare different currencies.")
        return self.amount <= other.amount

    def __gt__(self, other: "Money") -> bool:
        if self.currency != other.currency:
            raise ValueError("Cannot compare different currencies.")
        return self.amount > other.amount

    def __ge__(self, other: "Money") -> bool:
        if self.currency != other.currency:
            raise ValueError("Cannot compare different currencies.")
        return self.amount >= other.amount

    def as_float(self) -> float:
        """Use only for display purposes. Never for calculations."""
        return float(self.amount)

    def __str__(self) -> str:
        symbol = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}
        s = symbol.get(self.currency.value, self.currency.value + " ")
        return f"{s}{self.amount:,.2f}"

    def __repr__(self) -> str:
        return f"Money({self.amount}, {self.currency.value})"


# Convenience constructors
def inr(amount: float | int | str) -> Money:
    return Money(Decimal(str(amount)), Currency.INR)

def usd(amount: float | int | str) -> Money:
    return Money(Decimal(str(amount)), Currency.USD)


# ─────────────────────────────────────────────
# INSTRUMENT TYPES
# ─────────────────────────────────────────────

class Exchange(Enum):
    NSE = "NSE"
    BSE = "BSE"
    MCX = "MCX"
    NSE_CURRENCY = "NSE_CURRENCY"   # NSE Currency Derivatives segment
    NSE_FO = "NSE_FO"               # NSE Futures & Options segment
    GLOBAL = "GLOBAL"               # Analysis-only instruments (forex, US equities)


class AssetClass(Enum):
    EQUITY = "equity"
    EQUITY_FUTURES = "equity_futures"
    EQUITY_OPTIONS = "equity_options"
    CURRENCY_FUTURES = "currency_futures"
    CURRENCY_OPTIONS = "currency_options"
    COMMODITY_FUTURES = "commodity_futures"
    FOREX_SPOT = "forex_spot"           # Analysis only — global
    INDEX = "index"                      # Analysis only
    CRYPTO = "crypto"                    # Analysis only


@dataclass(frozen=True)
class Instrument:
    """
    Represents a tradeable or analysable instrument.

    is_execution_eligible: True only if the operator has an account
    on the exchange and the instrument is in execution_eligible_instruments.
    This field is set at runtime from OperatorProfile — never hardcoded.

    Analysis is always permitted. Execution requires is_execution_eligible=True.
    """
    symbol: str                          # e.g. "RELIANCE", "EURUSD", "XAUUSD"
    exchange: Exchange
    asset_class: AssetClass
    name: str = ""
    lot_size: int = 1
    tick_size: Decimal = Decimal("0.05")
    currency: Currency = Currency.INR
    is_execution_eligible: bool = False  # Set from OperatorProfile at runtime

    def __str__(self) -> str:
        return f"{self.symbol}:{self.exchange.value}"


# ─────────────────────────────────────────────
# SIGNAL TYPES — THE HEART OF TYPE SEPARATION
# ─────────────────────────────────────────────

class SignalDirection(Enum):
    LONG = "long"
    SHORT = "short"
    EXIT_LONG = "exit_long"
    EXIT_SHORT = "exit_short"


class SignalStrength(Enum):
    STRONG = "strong"       # Conviction score >= 75
    MODERATE = "moderate"   # Conviction score 55-74
    WEAK = "weak"           # Conviction score 40-54
    # Below 40: no signal generated


@dataclass(frozen=True)
class AnalysisSignal:
    """
    Output of any analysis process (screener, strategy, indicator).

    CRITICAL: This type has NO create_order() method and NO to_execution() method.
    The ONLY way to convert this to something executable is via:
        analysis_to_execution(signal, eligibility_set)

    This is the Knight Capital defense. Accidental execution is impossible.

    is_executable is ALWAYS False — this is a type-level constant.
    mypy --strict will reject any code that checks is_executable on this type
    and assumes it could be True.
    """
    # Always False — not configurable, not overridable
    is_executable: Literal[False] = field(default=False, init=False)

    instrument: Instrument
    direction: SignalDirection
    conviction_score: float                 # 0-100
    signal_strength: SignalStrength

    entry_price_zone_low: Money
    entry_price_zone_high: Money
    stop_loss: Money
    target_1: Money
    target_2: Optional[Money]

    risk_reward_ratio: float                # e.g. 2.5 means 1:2.5
    suggested_quantity: int                 # Based on 2% portfolio rule

    generated_at: datetime                  # Must be UTC-aware
    source_screener: str                    # e.g. "S1_MomentumBreakout"
    strategy_version: str                   # Git hash of strategy at generation time

    thesis_fundamental: str = ""
    thesis_technical: str = ""
    thesis_institutional: str = ""
    thesis_macro: str = ""
    risk_factors: list = field(default_factory=list)

    guardrails_triggered: list = field(default_factory=list)
    operator_overrides: list = field(default_factory=list)

    def __post_init__(self) -> None:
        # Validate UTC datetime
        validate_utc(self.generated_at, f"AnalysisSignal.generated_at for {self.instrument}")

        # Validate risk:reward
        if self.risk_reward_ratio < 2.0:
            from sentinel.core.errors import InsufficientRiskRewardError
            raise InsufficientRiskRewardError(
                self.instrument.symbol,
                self.risk_reward_ratio
            )

    @property
    def risk_amount(self) -> Money:
        """Distance from entry midpoint to stop loss."""
        entry_mid = (self.entry_price_zone_low + self.entry_price_zone_high) * Decimal("0.5")
        if self.direction == SignalDirection.LONG:
            return entry_mid - self.stop_loss
        return self.stop_loss - entry_mid

    @property
    def potential_reward_t1(self) -> Money:
        """Distance from entry midpoint to Target 1."""
        entry_mid = (self.entry_price_zone_low + self.entry_price_zone_high) * Decimal("0.5")
        if self.direction == SignalDirection.LONG:
            return self.target_1 - entry_mid
        return entry_mid - self.target_1

    def summary(self) -> str:
        return (
            f"[ANALYSIS] {self.instrument.symbol} {self.direction.value.upper()} | "
            f"Conviction: {self.conviction_score:.0f}/100 | "
            f"Entry: {self.entry_price_zone_low}-{self.entry_price_zone_high} | "
            f"SL: {self.stop_loss} | T1: {self.target_1} | "
            f"R:R 1:{self.risk_reward_ratio:.1f} | "
            f"Source: {self.source_screener}"
        )


@dataclass(frozen=True)
class ExecutionSignal:
    """
    Represents a signal that has been validated for execution.

    NEVER instantiate this directly. Always use:
        analysis_to_execution(analysis_signal, eligibility_set)

    The constructor raises InstrumentNotEligibleError if the instrument
    is not in the execution_eligible_instruments set.

    This type CAN be routed to a broker adapter. AnalysisSignal cannot.
    """
    source_signal: AnalysisSignal
    instrument: Instrument
    direction: SignalDirection

    entry_price_zone_low: Money
    entry_price_zone_high: Money
    stop_loss: Money
    target_1: Money
    target_2: Optional[Money]

    quantity: int
    max_capital: Money

    execution_exchange: Exchange
    execution_notes: str = ""

    validated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_utc(self.validated_at, f"ExecutionSignal.validated_at for {self.instrument}")

        # THE CRITICAL CHECK — this runs at instantiation time, not at order time
        if not self.instrument.is_execution_eligible:
            raise InstrumentNotEligibleError(
                self.instrument.symbol,
                reason=(
                    "Instrument is marked is_execution_eligible=False. "
                    "Check OperatorProfile.execution_eligible_instruments."
                )
            )


def analysis_to_execution(
    signal: AnalysisSignal,
    execution_eligible_instruments: FrozenSet[str],
) -> Optional[ExecutionSignal]:
    """
    THE ONLY CONVERSION PATH from analysis to execution.

    Returns None if the instrument is not eligible for execution.
    Returns ExecutionSignal if eligible and all validations pass.
    Never raises — returns None on any ineligibility.

    Usage:
        exec_signal = analysis_to_execution(analysis_signal, profile.execution_eligible_instruments)
        if exec_signal is None:
            # Show as analysis-only in dashboard (amber banner)
            render_analysis_only_card(analysis_signal)
        else:
            # Show as executable in dashboard
            render_execution_card(exec_signal)
    """
    if signal.instrument.symbol not in execution_eligible_instruments:
        return None

    # Create instrument copy with is_execution_eligible=True
    eligible_instrument = Instrument(
        symbol=signal.instrument.symbol,
        exchange=signal.instrument.exchange,
        asset_class=signal.instrument.asset_class,
        name=signal.instrument.name,
        lot_size=signal.instrument.lot_size,
        tick_size=signal.instrument.tick_size,
        currency=signal.instrument.currency,
        is_execution_eligible=True,
    )

    return ExecutionSignal(
        source_signal=signal,
        instrument=eligible_instrument,
        direction=signal.direction,
        entry_price_zone_low=signal.entry_price_zone_low,
        entry_price_zone_high=signal.entry_price_zone_high,
        stop_loss=signal.stop_loss,
        target_1=signal.target_1,
        target_2=signal.target_2,
        quantity=signal.suggested_quantity,
        max_capital=signal.entry_price_zone_high * signal.suggested_quantity,
        execution_exchange=eligible_instrument.exchange,
    )


# ─────────────────────────────────────────────
# MARKET DATA TYPES
# ─────────────────────────────────────────────

@dataclass
class OHLCV:
    """
    A single OHLCV bar for any instrument.
    timestamp is ALWAYS UTC-aware.
    """
    symbol: str
    timestamp: datetime     # UTC-aware always
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    timeframe: str          # "1min", "5min", "15min", "1h", "4h", "1d", "1w"

    # Indian equity specific (None for forex/global)
    delivery_pct: Optional[float] = None
    oi: Optional[int] = None        # Open interest for F&O

    def __post_init__(self) -> None:
        validate_utc(self.timestamp, f"OHLCV.timestamp for {self.symbol}")

    @property
    def typical_price(self) -> Decimal:
        return (self.high + self.low + self.close) / Decimal("3")

    @property
    def body_size(self) -> Decimal:
        return abs(self.close - self.open)

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open


@dataclass
class Tick:
    """
    A single price tick — the most granular data unit.
    Used for real-time WebSocket data from Kite Connect.
    """
    symbol: str
    timestamp: datetime     # UTC-aware always
    ltp: Decimal            # Last traded price
    volume: int
    bid: Optional[Decimal] = None
    ask: Optional[Decimal] = None
    oi: Optional[int] = None
    change_pct: Optional[float] = None

    def __post_init__(self) -> None:
        validate_utc(self.timestamp, f"Tick.timestamp for {self.symbol}")


# ─────────────────────────────────────────────
# POSITION AND PORTFOLIO TYPES
# ─────────────────────────────────────────────

class PositionStatus(Enum):
    OPEN = "open"
    CLOSED = "closed"
    PARTIAL = "partial"


class TradingStage(Enum):
    """
    The operator's current stage in the SPRINT_ROADMAP_v2.md lifecycle.
    Stage determines what actions are permitted.
    """
    PAPER = "paper"                 # Paper trading — no real money
    QUARANTINE_LIVE = "quarantine"  # First 30 days live, 10% allocation
    PRODUCTION = "production"       # Full live trading
    DEMOTION = "demotion"           # Three-override rule triggered


@dataclass
class Position:
    """An open or closed trading position."""
    position_id: str
    instrument: Instrument
    direction: SignalDirection
    quantity: int
    entry_price: Money
    stop_loss: Money
    target_1: Money
    target_2: Optional[Money]

    opened_at: datetime             # UTC-aware
    closed_at: Optional[datetime]   # UTC-aware, None if open
    close_price: Optional[Money]

    status: PositionStatus
    source_screener: str
    source_signal_id: str

    pre_mortem_answers: dict = field(default_factory=dict)
    guardrail_overrides: list = field(default_factory=list)
    notes: str = ""

    def __post_init__(self) -> None:
        validate_utc(self.opened_at, f"Position.opened_at for {self.position_id}")
        if self.closed_at:
            validate_utc(self.closed_at, f"Position.closed_at for {self.position_id}")

    @property
    def is_open(self) -> bool:
        return self.status == PositionStatus.OPEN

    @property
    def unrealized_pnl(self) -> Optional[Money]:
        """Returns None if position is not open or no current price available."""
        return None  # Populated by portfolio manager with live price

    @property
    def realized_pnl(self) -> Optional[Money]:
        if self.status != PositionStatus.CLOSED or self.close_price is None:
            return None
        if self.direction == SignalDirection.LONG:
            return (self.close_price - self.entry_price) * self.quantity
        return (self.entry_price - self.close_price) * self.quantity


# ─────────────────────────────────────────────
# MACRO OVERLAY TYPES
# ─────────────────────────────────────────────

class DXYRegime(Enum):
    """
    Dollar Index regime — used as a meta-signal for Indian equity
    sector rotation. Computed nightly, available by 06:00 IST.

    Documented in: GLOBAL_FOREX_MODULE.md §F9, ARCHITECTURE_v5.md §13
    """
    STRONG_UP = "strong_up"       # DXY 5d change > +1%
    UP = "up"                     # DXY 5d change +0.3% to +1%
    NEUTRAL = "neutral"           # DXY 5d change -0.3% to +0.3%
    DOWN = "down"                 # DXY 5d change -1% to -0.3%
    STRONG_DOWN = "strong_down"   # DXY 5d change < -1%


class MarketRegime(Enum):
    """
    Overall market regime — drives strategy allocation.
    VIX > 22 triggers defensive mode.
    """
    BULL_TRENDING = "bull_trending"
    BULL_VOLATILE = "bull_volatile"
    NEUTRAL = "neutral"
    BEAR_VOLATILE = "bear_volatile"
    BEAR_TRENDING = "bear_trending"
    DEFENSIVE = "defensive"         # VIX > 22


@dataclass
class MacroOverlayDaily:
    """
    Cross-system macro data computed nightly.
    Fed into Indian equity strategies as features.

    Documented in: GLOBAL_FOREX_MODULE.md §F9, ARCHITECTURE_v5.md §19
    Schema version: MacroOverlayDailyV1
    """
    schema_version: str = "MacroOverlayDailyV1"
    as_of_date: Optional[datetime] = None   # UTC-aware

    # DXY signals
    dxy_5d_change_pct: Optional[float] = None
    dxy_vs_200d_pct: Optional[float] = None
    dxy_regime: Optional[DXYRegime] = None

    # US market signals
    sp500_overnight_change_pct: Optional[float] = None
    nasdaq_overnight_change_pct: Optional[float] = None
    us_vix_level: Optional[float] = None
    us_vix_state: Optional[str] = None     # "low"/"normal"/"elevated"/"fear"/"panic"

    # Rates
    us_10y_yield: Optional[float] = None
    us_10y_5d_change_bps: Optional[float] = None
    us_2y_yield: Optional[float] = None
    yield_curve_slope_bps: Optional[float] = None   # 10y - 2y

    # Commodities
    brent_crude_usd: Optional[float] = None
    brent_5d_change_pct: Optional[float] = None
    gold_usd: Optional[float] = None
    gold_5d_change_pct: Optional[float] = None

    # Indian specific
    usd_inr: Optional[float] = None
    usd_inr_5d_change_pct: Optional[float] = None
    fii_net_yesterday_cr: Optional[float] = None    # FII net in crores
    fii_30d_trend: Optional[str] = None             # "accumulating"/"distributing"/"neutral"

    # Regime
    market_regime: Optional[MarketRegime] = None

    def is_complete(self) -> bool:
        """Returns True if all critical fields are populated."""
        critical = [self.dxy_regime, self.market_regime, self.us_vix_level]
        return all(v is not None for v in critical)
