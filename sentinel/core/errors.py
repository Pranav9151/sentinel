"""
sentinel/core/errors.py
=======================
All custom exceptions for Project Sentinel.

Design principle: every failure mode documented in FORENSIC_ANALYSIS_v5.md
has a corresponding exception class here. This makes error handling
explicit and traceable back to the architecture documents.

Never raise generic Exception or ValueError in Sentinel code.
Always raise a specific exception from this module.
"""


# ─────────────────────────────────────────────
# TYPE SYSTEM ERRORS
# ─────────────────────────────────────────────

class SentinelError(Exception):
    """Base class for all Sentinel exceptions."""
    pass


class InstrumentNotEligibleError(SentinelError):
    """
    Raised when code attempts to create an ExecutionSignal for an
    instrument that is not in the operator's execution_eligible_instruments set.

    This is the Knight Capital defense — it is IMPOSSIBLE to accidentally
    route an analysis signal to execution. The type system enforces this,
    not policy.

    Documented in: ARCHITECTURE_v5.md §2.3, FORENSIC_ANALYSIS_v5.md §2.19.6
    """
    def __init__(self, symbol: str, reason: str = ""):
        self.symbol = symbol
        self.reason = reason
        super().__init__(
            f"Instrument '{symbol}' is not eligible for execution. "
            f"Analysis signals for this instrument cannot be converted "
            f"to execution signals. {reason}".strip()
        )


class AnalysisToExecutionConversionError(SentinelError):
    """
    Raised when analysis_to_execution() conversion fails for reasons
    other than eligibility (e.g. missing required fields).
    """
    pass


# ─────────────────────────────────────────────
# CONFIGURATION ERRORS
# ─────────────────────────────────────────────

class ConfigurationError(SentinelError):
    """
    Raised when config.yaml or .env is missing required fields,
    has invalid values, or fails validation.

    Documented in: FORENSIC_ANALYSIS_v5.md §2.2
    """
    pass


class OperatorProfileNotFoundError(ConfigurationError):
    """Raised when operator profile cannot be loaded from config."""
    pass


class SignoffNotFoundError(ConfigurationError):
    """
    Raised when Sprint 6 or Sprint 7 entry is attempted without
    a valid §7.6 operator sign-off commit hash in the profile.

    Documented in: ARCHITECTURE_v5.md §7.6, SPRINT_ROADMAP_v2.md Sprint 6
    """
    pass


# ─────────────────────────────────────────────
# DATA LAYER ERRORS
# ─────────────────────────────────────────────

class DataError(SentinelError):
    """Base class for data layer errors."""
    pass


class StaleDataError(DataError):
    """
    Raised when data freshness requirement is not met.
    Each screener specifies maximum acceptable data age.
    If data is older than that threshold, this is raised.

    Documented in: SCREENERS_MODULE_SPEC.md (per screener input spec)
    """
    def __init__(self, source: str, age_minutes: float, max_age_minutes: float):
        self.source = source
        self.age_minutes = age_minutes
        self.max_age_minutes = max_age_minutes
        super().__init__(
            f"Data from '{source}' is {age_minutes:.1f} minutes old. "
            f"Maximum allowed age is {max_age_minutes:.1f} minutes."
        )


class DataSourceUnavailableError(DataError):
    """
    Raised when a primary data source is completely unreachable.
    Triggers fallback logic in connector layer.

    Documented in: FORENSIC_ANALYSIS_v5.md §2.3
    """
    def __init__(self, source: str, reason: str = ""):
        self.source = source
        super().__init__(f"Data source '{source}' is unavailable. {reason}".strip())


class NaiveDatetimeError(DataError):
    """
    Raised when a naive datetime (without timezone) is detected anywhere
    in the system. ALL datetimes in Sentinel must be UTC-aware.

    This is enforced by ruff DTZ lint rules AND at runtime by this exception.
    A naive datetime is a silent bug that causes incorrect data alignment.

    Documented in: FORENSIC_ANALYSIS_v5.md §2.1
    """
    def __init__(self, location: str = ""):
        super().__init__(
            f"Naive datetime detected{' at ' + location if location else ''}. "
            f"All datetimes in Sentinel must be timezone-aware UTC. "
            f"Use datetime.now(timezone.utc) not datetime.now()."
        )


class SurvivorsOnlyDataError(DataError):
    """
    Raised when a data source only contains currently-listed instruments
    (survivorship bias). The backtest engine requires delisted instruments
    to be present in historical data.

    Documented in: GLOBAL_FAILURES_PLAYBOOK.md §1.3
    """
    pass


# ─────────────────────────────────────────────
# FEATURE STORE ERRORS
# ─────────────────────────────────────────────

class FeatureStoreError(SentinelError):
    """Base class for feature store errors."""
    pass


class LookaheadBiasError(FeatureStoreError):
    """
    Raised when a feature computation would use future data
    relative to the as_of timestamp.

    This is the single most common reason backtests look good and fail live.
    Every feature computation must be validated against this.

    Documented in: GLOBAL_FAILURES_PLAYBOOK.md §1.3, FORENSIC_ANALYSIS_v5.md §2.4
    """
    def __init__(self, feature_name: str, data_timestamp: str, as_of_timestamp: str):
        super().__init__(
            f"LOOKAHEAD BIAS DETECTED in feature '{feature_name}'. "
            f"Data timestamp {data_timestamp} is AFTER as_of timestamp {as_of_timestamp}. "
            f"This would inflate backtest performance. Feature computation blocked."
        )


class FeatureNotFoundError(FeatureStoreError):
    """Raised when a required feature is not available in the feature store."""
    def __init__(self, feature_name: str, symbol: str = "", as_of: str = ""):
        self.feature_name = feature_name
        super().__init__(
            f"Feature '{feature_name}' not found"
            f"{' for ' + symbol if symbol else ''}"
            f"{' as of ' + as_of if as_of else ''}."
        )


# ─────────────────────────────────────────────
# RISK AND ORDER ERRORS
# ─────────────────────────────────────────────

class RiskError(SentinelError):
    """Base class for risk management errors."""
    pass


class PositionSizeTooLargeError(RiskError):
    """
    Raised when a proposed position size exceeds the operator's
    risk parameters (2% portfolio rule or per-screener caps).

    Documented in: ARCHITECTURE_v5.md §9, SCREENERS_MODULE_SPEC.md §S4
    """
    def __init__(self, symbol: str, proposed_pct: float, max_pct: float):
        super().__init__(
            f"Position size for '{symbol}' ({proposed_pct:.2f}% of portfolio) "
            f"exceeds maximum allowed ({max_pct:.2f}%). Order rejected."
        )


class MonthlyLossLimitReachedError(RiskError):
    """
    Raised when monthly loss circuit breaker is triggered.
    No new positions allowed until next month or operator review.

    Documented in: ARCHITECTURE_v5.md §9.1
    """
    pass


class InsufficientRiskRewardError(RiskError):
    """
    Raised when a trade setup has risk:reward below the minimum 1:2 threshold.
    Sentinel never generates a card for a sub-threshold setup.

    Documented in: ARCHITECTURE_v5.md §9
    """
    def __init__(self, symbol: str, rr_ratio: float, min_rr: float = 2.0):
        super().__init__(
            f"Risk:Reward ratio for '{symbol}' is 1:{rr_ratio:.1f}, "
            f"below minimum 1:{min_rr}. Trade card not generated."
        )


# ─────────────────────────────────────────────
# BEHAVIORAL GUARDRAIL ERRORS
# ─────────────────────────────────────────────

class GuardrailTriggeredError(SentinelError):
    """
    Raised when a behavioral guardrail blocks an action.
    The operator can override (logged) but cannot silently bypass.

    Documented in: ARCHITECTURE_v5.md §23
    """
    def __init__(self, guardrail_name: str, reason: str, can_override: bool = True):
        self.guardrail_name = guardrail_name
        self.can_override = can_override
        override_msg = (
            " This guardrail CAN be overridden with logged justification."
            if can_override else
            " This guardrail CANNOT be overridden."
        )
        super().__init__(f"Guardrail '{guardrail_name}' triggered: {reason}.{override_msg}")


class ThreeOverrideRuleError(GuardrailTriggeredError):
    """
    Raised when the Three-Override Rule (Guardrail #9) triggers.
    This automatically demotes the system to paper trading mode.
    Cannot be overridden — only a fresh §7.6 sign-off re-enables live mode.

    Documented in: ARCHITECTURE_v5.md §23.9, SPRINT_ROADMAP_v2.md §R8.3
    """
    def __init__(self, override_count: int, window_days: int = 30):
        super().__init__(
            guardrail_name="ThreeOverrideRule",
            reason=(
                f"{override_count} guardrail overrides logged in the last {window_days} days. "
                f"System automatically demoted to paper trading for 14 days. "
                f"A fresh §7.6 sign-off is required to resume live trading."
            ),
            can_override=False
        )


class GSMASMRejectionError(GuardrailTriggeredError):
    """
    Raised when a trade is attempted on a GSM/ASM surveillance-listed stock.
    Hard rejection — cannot be overridden.

    Documented in: ARCHITECTURE_v5.md §23.7, GLOBAL_FAILURES_PLAYBOOK.md §1.16
    """
    def __init__(self, symbol: str, list_type: str = "GSM/ASM"):
        super().__init__(
            guardrail_name="GSMASMRejection",
            reason=f"'{symbol}' is on the {list_type} surveillance list. Hard rejection.",
            can_override=False
        )


# ─────────────────────────────────────────────
# KILL SWITCH ERRORS
# ─────────────────────────────────────────────

class KillSwitchError(SentinelError):
    """Base class for kill switch errors."""
    pass


class KillSwitchActivatedError(KillSwitchError):
    """
    Raised throughout the system when the kill switch is active.
    All order generation is blocked. Position management (stops) continues.

    Documented in: ARCHITECTURE_v5.md §11, FORENSIC_ANALYSIS_v5.md §2.9
    """
    pass


class KillSwitchTestFailedError(KillSwitchError):
    """
    Raised when the monthly kill switch test fails.
    Sprint acceptance gate: kill switch must flatten paper positions in < 5 seconds.
    """
    def __init__(self, elapsed_seconds: float, max_seconds: float = 5.0):
        super().__init__(
            f"Kill switch test FAILED. Took {elapsed_seconds:.1f}s to flatten positions. "
            f"Maximum allowed: {max_seconds}s. Do not proceed to live trading until resolved."
        )


# ─────────────────────────────────────────────
# SCREENER ERRORS
# ─────────────────────────────────────────────

class ScreenerError(SentinelError):
    """Base class for screener errors."""
    pass


class ScreenerDataStalenessError(ScreenerError):
    """
    Raised when a screener's required data is too stale to run.
    The screener blocks rather than running on bad data.
    """
    pass


class ScreenerResultCapExceededError(ScreenerError):
    """
    Raised when screener logic attempts to return more results than
    the hard cap (e.g. S4 Penny/Small Cap: max 5 results always).
    """
    pass


# ─────────────────────────────────────────────
# MOCK MODE MARKER
# ─────────────────────────────────────────────

class MockModeActiveNotice(SentinelError):
    """
    Not an error — a deliberate marker raised when the system
    detects that MOCK_MODE=true and an action requires real API access.
    Used during build phase when real API keys are not yet configured.

    When you see this, it means the code path is correct and working —
    it just needs a real API key to do real work.
    """
    def __init__(self, api_name: str):
        super().__init__(
            f"MOCK MODE: '{api_name}' API call skipped. "
            f"Set MOCK_MODE=false and add your API key to .env to use real data."
        )
