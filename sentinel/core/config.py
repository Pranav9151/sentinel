"""
sentinel/core/config.py
=======================
OperatorProfile schema and configuration loader.

All system behaviour adapts to the operator's profile.
Position sizes, risk caps, active trading styles, SIP budget,
and execution-eligible instruments all flow from this single source.

In MOCK_MODE (during build phase), real API keys are not required.
Set MOCK_MODE=true in .env to build and test the full system
without any paid subscriptions.

Documented in: ARCHITECTURE_v5.md §22
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import FrozenSet, Optional

import yaml
from dotenv import load_dotenv

from sentinel.core.errors import ConfigurationError, OperatorProfileNotFoundError

# Load .env file at import time
load_dotenv()


# ─────────────────────────────────────────────
# ENVIRONMENT HELPERS
# ─────────────────────────────────────────────

def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)

def _env_bool(key: str, default: bool = False) -> bool:
    val = os.getenv(key, str(default)).lower()
    return val in ("true", "1", "yes")

def _env_required(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise ConfigurationError(
            f"Required environment variable '{key}' is not set. "
            f"Add it to your .env file. See .env.example for reference."
        )
    return val


# ─────────────────────────────────────────────
# TRADING STYLE CONFIG
# ─────────────────────────────────────────────

@dataclass
class TradingStylesConfig:
    """
    Which trading styles are active for the operator.
    Inactive styles are completely skipped — no signals generated,
    no screeners run, no cards produced.
    """
    intraday: bool = False          # Sprint 7+ — deferred
    swing: bool = True              # 2-10 days — active from Sprint 3
    positional: bool = True         # 1-3 months — active from Sprint 3
    long_term: bool = True          # 1yr+ — active from Sprint 3
    mutual_funds: bool = True       # SIP advisory — active from Sprint 4
    forex_usd_inr: bool = True      # INR pairs on NSE — Sprint 4
    forex_global: bool = True       # Global analysis only — Sprint 4
    mcx: bool = False               # MCX commodities — Sprint 5+


@dataclass
class ForexStylesConfig:
    """Forex-specific configuration."""
    usd_inr_active: bool = True
    usd_inr_special_handling: bool = True    # RBI intervention risk: 0.5x position cap
    eur_inr_active: bool = True
    gbp_inr_active: bool = True
    jpy_inr_active: bool = True
    global_analysis_active: bool = True       # EUR/USD, Gold etc — analysis only
    mcx_execution_enabled: bool = False       # Sprint 5+ onboarding


# ─────────────────────────────────────────────
# RISK CONFIG
# ─────────────────────────────────────────────

@dataclass
class RiskConfig:
    """
    Risk parameters. All position sizing derives from these.
    Documented in: ARCHITECTURE_v5.md §9
    """
    # Per-trade risk (Layer 1 — deterministic hard caps)
    max_risk_per_trade_pct: float = 1.0         # 1% of portfolio per trade
    max_total_open_risk_pct: float = 6.0        # Max 6% total open risk
    min_risk_reward_ratio: float = 2.0          # Minimum 1:2 R:R

    # Position concentration caps
    max_single_stock_pct: float = 10.0          # Max 10% in one stock
    max_sector_pct: float = 25.0                # Max 25% in one sector
    max_s4_aggregate_pct: float = 2.0           # S4 (Penny) aggregate cap
    max_s4_single_pct: float = 0.5              # S4 single stock cap

    # USD/INR special handling (RBI intervention risk)
    usd_inr_position_multiplier: float = 0.5   # 0.5x normal size for USD/INR

    # Monthly loss circuit breaker
    max_monthly_loss_pct: float = 5.0           # 5% monthly loss triggers review

    # VIX-based regime
    vix_defensive_threshold: float = 22.0       # VIX > 22 = defensive mode


# ─────────────────────────────────────────────
# SIP CONFIG
# ─────────────────────────────────────────────

@dataclass
class SIPConfig:
    """Monthly SIP configuration."""
    total_monthly_sip_budget_inr: Decimal = Decimal("0")
    emergency_fund_monthly_inr: Decimal = Decimal("1500")   # Per Failures Playbook §6.4
    sentinel_sip_inr: Decimal = Decimal("1500")             # Remainder after emergency fund
    step_up_pct_per_year: float = 10.0                      # Increase SIP 10% each April


# ─────────────────────────────────────────────
# TAX CONFIG
# ─────────────────────────────────────────────

@dataclass
class TaxConfig:
    """
    Indian resident tax configuration (post-Budget 2024-25).
    Documented in: ARCHITECTURE_v5.md §21, CHANGELOG §A2
    """
    stcg_rate_pct: float = 20.0             # Short-term capital gains
    ltcg_rate_pct: float = 12.5            # Long-term capital gains
    ltcg_exemption_inr: Decimal = Decimal("125000")  # ₹1.25L annual exemption
    ltcg_holding_days: int = 365            # Must hold > 1 year for LTCG
    fo_income_type: str = "business"        # F&O = business income, slab rate
    financial_year_start_month: int = 4     # April


# ─────────────────────────────────────────────
# OPERATOR PROFILE — THE MASTER CONFIG
# ─────────────────────────────────────────────

@dataclass
class OperatorProfile:
    """
    The complete operator configuration. Everything in Sentinel
    adapts to this profile.

    Fill this via config.yaml — do NOT hardcode values in Python.
    API keys go in .env — do NOT put them in config.yaml.

    Documented in: ARCHITECTURE_v5.md §22
    """
    # Identity
    operator_name: str = "Operator"
    location: str = "Pune, Maharashtra, India"
    is_currently_indian_resident: bool = True

    # Portfolio
    total_portfolio_value_inr: Decimal = Decimal("300000")  # ₹3 lakh default
    emergency_fund_months_confirmed: int = 0                 # Sprint 6 gate requires >= 6

    # Trading stage (from SPRINT_ROADMAP_v2.md)
    # paper → quarantine_live → production
    # demotion triggers automatically via Three-Override Rule
    trading_stage: str = "paper"    # "paper", "quarantine", "production", "demotion"

    # Sign-off (required before Sprint 6)
    section_7_6_signoff_commit_hash: str = ""   # Git commit hash of signed §7.6

    # Trading styles
    styles: TradingStylesConfig = field(default_factory=TradingStylesConfig)
    forex: ForexStylesConfig = field(default_factory=ForexStylesConfig)

    # Risk
    risk: RiskConfig = field(default_factory=RiskConfig)

    # SIP
    sip: SIPConfig = field(default_factory=SIPConfig)

    # Tax
    tax: TaxConfig = field(default_factory=TaxConfig)

    # Time availability (IST)
    morning_window_start_ist: str = "07:00"
    morning_window_end_ist: str = "09:30"
    evening_window_start_ist: str = "19:00"
    evening_window_end_ist: str = "23:00"

    # Instruments eligible for execution
    # These are loaded from config.yaml — not hardcoded
    execution_eligible_instruments: FrozenSet[str] = field(
        default_factory=lambda: frozenset([
            # NSE Cash Equity (all Nifty 500 eligible by default)
            # Specific symbols added here for F&O, MCX, Currency
            "USDINR",   # NSE Currency Derivatives
            "EURINR",
            "GBPINR",
            "JPYINR",
        ])
    )

    # Broker config
    zerodha_user_id: str = ""       # From .env: ZERODHA_USER_ID
    zerodha_api_key: str = ""       # From .env: ZERODHA_API_KEY
    zerodha_api_secret: str = ""    # From .env: ZERODHA_API_SECRET

    @property
    def max_risk_per_trade_inr(self) -> Decimal:
        """Maximum INR risk per trade (1% of portfolio)."""
        return self.total_portfolio_value_inr * Decimal(
            str(self.risk.max_risk_per_trade_pct / 100)
        )

    @property
    def max_total_open_risk_inr(self) -> Decimal:
        """Maximum total open risk across all positions."""
        return self.total_portfolio_value_inr * Decimal(
            str(self.risk.max_total_open_risk_pct / 100)
        )

    @property
    def is_live_trading_enabled(self) -> bool:
        """True only in quarantine or production stage."""
        return self.trading_stage in ("quarantine", "production")

    @property
    def is_paper_mode(self) -> bool:
        return self.trading_stage in ("paper", "demotion")

    def calculate_position_size(
        self,
        entry_price: Decimal,
        stop_loss_price: Decimal,
        risk_multiplier: float = 1.0,
    ) -> int:
        """
        Calculate position size using the Clenow/2%-risk formula.

        Shares = (Portfolio × Risk%) / (Entry - Stop Loss)

        Args:
            entry_price: Planned entry price
            stop_loss_price: Planned stop loss price
            risk_multiplier: 0.5 for USD/INR, 1.0 standard, 0.5 for S4 penny stocks

        Returns:
            Number of shares/lots to buy (always >= 1)
        """
        risk_per_share = abs(entry_price - stop_loss_price)
        if risk_per_share == 0:
            return 0

        max_risk = self.max_risk_per_trade_inr * Decimal(str(risk_multiplier))
        shares = max_risk / risk_per_share

        return max(1, int(shares))

    def validate_sprint6_ready(self) -> list[str]:
        """
        Returns list of blockers for Sprint 6 (real money).
        Empty list = ready. Non-empty = still blocked.
        """
        blockers = []
        if self.emergency_fund_months_confirmed < 6:
            blockers.append(
                f"Emergency fund: {self.emergency_fund_months_confirmed} months confirmed, "
                f"need 6 months before live trading."
            )
        if not self.section_7_6_signoff_commit_hash:
            blockers.append(
                "§7.6 operator sign-off not committed. "
                "Complete and commit the sign-off document before live trading."
            )
        if not self.zerodha_api_key:
            blockers.append("Zerodha API key not configured.")
        return blockers


# ─────────────────────────────────────────────
# CONFIG LOADER
# ─────────────────────────────────────────────

def load_config(config_path: Optional[Path] = None) -> OperatorProfile:
    """
    Load the operator profile from config.yaml and .env.

    API keys come from .env (never from config.yaml).
    Everything else comes from config.yaml.

    In MOCK_MODE, API keys are not required.
    """
    if config_path is None:
        config_path = Path("config.yaml")

    if not config_path.exists():
        raise OperatorProfileNotFoundError(
            f"config.yaml not found at {config_path.absolute()}. "
            f"Copy config.yaml.example to config.yaml and fill in your details."
        )

    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if raw is None:
        raise ConfigurationError("config.yaml is empty.")

    operator_section = raw.get("operator", {})
    risk_section = raw.get("risk", {})
    sip_section = raw.get("sip", {})
    styles_section = raw.get("trading_styles", {})
    forex_section = raw.get("forex", {})

    # Build risk config
    risk = RiskConfig(
        max_risk_per_trade_pct=risk_section.get("max_risk_per_trade_pct", 1.0),
        max_total_open_risk_pct=risk_section.get("max_total_open_risk_pct", 6.0),
        min_risk_reward_ratio=risk_section.get("min_risk_reward_ratio", 2.0),
        max_single_stock_pct=risk_section.get("max_single_stock_pct", 10.0),
        max_sector_pct=risk_section.get("max_sector_pct", 25.0),
        max_s4_aggregate_pct=risk_section.get("max_s4_aggregate_pct", 2.0),
        max_s4_single_pct=risk_section.get("max_s4_single_pct", 0.5),
        max_monthly_loss_pct=risk_section.get("max_monthly_loss_pct", 5.0),
        vix_defensive_threshold=risk_section.get("vix_defensive_threshold", 22.0),
    )

    # Build SIP config
    sip = SIPConfig(
        total_monthly_sip_budget_inr=Decimal(
            str(sip_section.get("total_monthly_budget_inr", 3000))
        ),
        emergency_fund_monthly_inr=Decimal(
            str(sip_section.get("emergency_fund_monthly_inr", 1500))
        ),
        sentinel_sip_inr=Decimal(
            str(sip_section.get("sentinel_sip_inr", 1500))
        ),
        step_up_pct_per_year=sip_section.get("step_up_pct_per_year", 10.0),
    )

    # Build trading styles
    styles = TradingStylesConfig(
        intraday=styles_section.get("intraday", False),
        swing=styles_section.get("swing", True),
        positional=styles_section.get("positional", True),
        long_term=styles_section.get("long_term", True),
        mutual_funds=styles_section.get("mutual_funds", True),
        forex_usd_inr=styles_section.get("forex_usd_inr", True),
        forex_global=styles_section.get("forex_global", True),
        mcx=styles_section.get("mcx", False),
    )

    # Build forex config
    forex = ForexStylesConfig(
        usd_inr_active=forex_section.get("usd_inr_active", True),
        usd_inr_special_handling=forex_section.get("usd_inr_special_handling", True),
        eur_inr_active=forex_section.get("eur_inr_active", True),
        gbp_inr_active=forex_section.get("gbp_inr_active", True),
        jpy_inr_active=forex_section.get("jpy_inr_active", True),
        global_analysis_active=forex_section.get("global_analysis_active", True),
        mcx_execution_enabled=forex_section.get("mcx_execution_enabled", False),
    )

    # Load API keys from environment (never from config.yaml)
    profile = OperatorProfile(
        operator_name=operator_section.get("name", "Operator"),
        location=operator_section.get("location", "Pune, Maharashtra, India"),
        is_currently_indian_resident=operator_section.get(
            "is_currently_indian_resident", True
        ),
        total_portfolio_value_inr=Decimal(
            str(operator_section.get("total_portfolio_value_inr", 300000))
        ),
        emergency_fund_months_confirmed=operator_section.get(
            "emergency_fund_months_confirmed", 0
        ),
        trading_stage=operator_section.get("trading_stage", "paper"),
        section_7_6_signoff_commit_hash=operator_section.get(
            "section_7_6_signoff_commit_hash", ""
        ),
        styles=styles,
        forex=forex,
        risk=risk,
        sip=sip,
        execution_eligible_instruments=frozenset(
            raw.get("execution_eligible_instruments", [
                "USDINR", "EURINR", "GBPINR", "JPYINR"
            ])
        ),
        # API keys from environment
        zerodha_user_id=_env("ZERODHA_USER_ID"),
        zerodha_api_key=_env("ZERODHA_API_KEY"),
        zerodha_api_secret=_env("ZERODHA_API_SECRET"),
    )

    return profile


# Global profile instance — loaded once at startup
_profile: Optional[OperatorProfile] = None

def get_profile() -> OperatorProfile:
    """Get the loaded operator profile. Call load_config() first."""
    global _profile
    if _profile is None:
        _profile = load_config()
    return _profile

def set_profile(profile: OperatorProfile) -> None:
    """Set profile directly — used in tests."""
    global _profile
    _profile = profile
