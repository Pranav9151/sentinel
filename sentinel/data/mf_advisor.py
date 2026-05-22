"""
sentinel/data/mf_advisor.py
==============================
Mutual Fund Advisory Engine.

Provides:
  - SIP recommendation based on operator goals
  - Fund quality scoring
  - Scenario-based SIP decision engine (should I pause/continue/add?)
  - Tax optimisation alerts (LTCG eligibility, harvest opportunities)

Data source: AMFI monthly portfolio disclosures (mock in MOCK_MODE).

Documented in: ARCHITECTURE_v5.md §18, SCREENERS_MODULE_SPEC.md §S6
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


logger = logging.getLogger(__name__)
MOCK_MODE = os.getenv("MOCK_MODE", "true").lower() == "true"


# ─────────────────────────────────────────────
# FUND DATABASE (mock — AMFI in live mode)
# ─────────────────────────────────────────────

MOCK_FUNDS: dict[str, dict] = {
    "PPFAS_FLEXI": {
        "name":         "Parag Parikh Flexi Cap Fund",
        "house":        "PPFAS",
        "category":     "Flexi Cap",
        "returns_1y":   18.2,  "returns_3y": 16.8, "returns_5y": 22.4,
        "returns_10y":  19.1,
        "benchmark_5y": 14.2,
        "alpha_5y":     8.2,
        "expense":      0.59,
        "aum_cr":       75000,
        "manager":      "Rajeev Thakkar",
        "manager_yrs":  12,
        "downside_capture": 72,
        "upside_capture":   105,
        "sharpe":       1.42,
        "sortino":      1.68,
        "std_dev":      14.2,
        "pe_portfolio": 23.1,
        "risk":         "moderate",
        "category_rank":1,
    },
    "MIRAE_LARGECAP": {
        "name":         "Mirae Asset Large Cap Fund",
        "house":        "Mirae Asset",
        "category":     "Large Cap",
        "returns_1y":   15.4,  "returns_3y": 14.2, "returns_5y": 18.6,
        "returns_10y":  16.8,
        "benchmark_5y": 14.0,
        "alpha_5y":     4.6,
        "expense":      0.54,
        "aum_cr":       38000,
        "manager":      "Gaurav Khandelwal",
        "manager_yrs":  5,
        "downside_capture": 85,
        "upside_capture":   98,
        "sharpe":       1.18,
        "sortino":      1.35,
        "std_dev":      13.4,
        "pe_portfolio": 22.8,
        "risk":         "moderate",
        "category_rank":2,
    },
    "NIPPON_SMALLCAP": {
        "name":         "Nippon India Small Cap Fund",
        "house":        "Nippon India",
        "category":     "Small Cap",
        "returns_1y":   22.1,  "returns_3y": 28.4, "returns_5y": 34.2,
        "returns_10y":  24.6,
        "benchmark_5y": 18.2,
        "alpha_5y":     16.0,
        "expense":      0.64,
        "aum_cr":       52000,
        "manager":      "Samir Rachh",
        "manager_yrs":  8,
        "downside_capture": 115,
        "upside_capture":   128,
        "sharpe":       1.38,
        "sortino":      1.52,
        "std_dev":      22.8,
        "pe_portfolio": 26.4,
        "risk":         "high",
        "category_rank":1,
    },
    "ICICI_BALANCED": {
        "name":         "ICICI Prudential Balanced Advantage",
        "house":        "ICICI Prudential",
        "category":     "Balanced Advantage",
        "returns_1y":   12.8,  "returns_3y": 13.2, "returns_5y": 14.8,
        "returns_10y":  13.4,
        "benchmark_5y": 11.0,
        "alpha_5y":     3.8,
        "expense":      0.78,
        "aum_cr":       62000,
        "manager":      "Ihab Dalwai",
        "manager_yrs":  6,
        "downside_capture": 62,
        "upside_capture":   88,
        "sharpe":       1.22,
        "sortino":      1.41,
        "std_dev":      10.8,
        "pe_portfolio": 20.2,
        "risk":         "conservative",
        "category_rank":1,
    },
    "QUANT_FLEXI": {
        "name":         "Quant Flexi Cap Fund",
        "house":        "Quant MF",
        "category":     "Flexi Cap",
        "returns_1y":   28.4,  "returns_3y": 32.1, "returns_5y": 38.6,
        "returns_10y":  None,
        "benchmark_5y": 14.2,
        "alpha_5y":     24.4,
        "expense":      0.59,
        "aum_cr":       8200,
        "manager":      "Ankit Pande",
        "manager_yrs":  4,
        "downside_capture": 128,
        "upside_capture":   148,
        "sharpe":       1.56,
        "sortino":      1.78,
        "std_dev":      26.4,
        "pe_portfolio": 28.4,
        "risk":         "very_high",
        "category_rank":2,
    },
    "HDFC_MIDCAP": {
        "name":         "HDFC Mid-Cap Opportunities Fund",
        "house":        "HDFC MF",
        "category":     "Mid Cap",
        "returns_1y":   19.8,  "returns_3y": 22.4, "returns_5y": 28.6,
        "returns_10y":  21.2,
        "benchmark_5y": 16.4,
        "alpha_5y":     12.2,
        "expense":      0.72,
        "aum_cr":       68000,
        "manager":      "Chirag Setalvad",
        "manager_yrs":  14,
        "downside_capture": 98,
        "upside_capture":   112,
        "sharpe":       1.32,
        "sortino":      1.48,
        "std_dev":      18.4,
        "pe_portfolio": 25.6,
        "risk":         "high",
        "category_rank":1,
    },
}

# Quality tier — fund codes that count as "quality" for S6 screener
QUALITY_TIER = {"PPFAS_FLEXI", "MIRAE_LARGECAP", "NIPPON_SMALLCAP",
                "ICICI_BALANCED", "HDFC_MIDCAP"}


@dataclass
class SIPRecommendation:
    """A single SIP recommendation for a fund."""
    fund_key:   str
    fund_name:  str
    category:   str
    monthly_amount: Decimal
    reason:     str
    allocation_pct: float
    risk_level: str
    key_metrics: dict = field(default_factory=dict)


class MFAdvisor:
    """
    Mutual Fund advisory engine.

    Usage:
        advisor = MFAdvisor()
        recs = advisor.recommend_sip(
            monthly_budget=3000,
            risk_appetite="moderate",
            time_horizon_years=10,
            goal="wealth_creation",
        )
        scenario = advisor.sip_scenario("market_crash", nifty_drop_pct=38)
    """

    def __init__(self) -> None:
        self.funds = MOCK_FUNDS

    # ─────────────────────────────────────────────
    # FUND QUALITY SCORING
    # ─────────────────────────────────────────────

    def score_fund(self, fund_key: str) -> dict[str, Any]:
        """Score a fund 0-100 across multiple quality dimensions."""
        f = self.funds.get(fund_key)
        if not f:
            return {"error": f"Fund {fund_key} not found"}

        score = 0.0
        breakdown = {}

        # 1. Alpha (5yr vs benchmark) — max 25 pts
        alpha = f.get("alpha_5y", 0) or 0
        alpha_pts = min(alpha * 2, 25)
        score += alpha_pts
        breakdown["alpha_5y"] = {"value": alpha, "points": round(alpha_pts, 1)}

        # 2. Sharpe ratio — max 20 pts
        sharpe = f.get("sharpe", 0) or 0
        sharpe_pts = min(sharpe * 10, 20)
        score += sharpe_pts
        breakdown["sharpe"] = {"value": sharpe, "points": round(sharpe_pts, 1)}

        # 3. Downside capture (lower = better) — max 20 pts
        dc = f.get("downside_capture", 100) or 100
        dc_pts = max(0, (100 - dc) * 0.4)   # 80 capture = 8pts, 60 = 16pts
        score += dc_pts
        breakdown["downside_capture"] = {"value": dc, "points": round(dc_pts, 1)}

        # 4. Manager tenure — max 15 pts
        tenure = f.get("manager_yrs", 0) or 0
        tenure_pts = min(tenure * 1.5, 15)
        score += tenure_pts
        breakdown["manager_tenure"] = {"value": tenure, "points": round(tenure_pts, 1)}

        # 5. Expense ratio (lower = better) — max 10 pts
        exp = f.get("expense", 2.0) or 2.0
        exp_pts = max(0, (2.0 - exp) * 10)
        score += exp_pts
        breakdown["expense_ratio"] = {"value": exp, "points": round(exp_pts, 1)}

        # 6. AUM (adequate but not bloated) — max 10 pts
        aum = f.get("aum_cr", 0) or 0
        if 2000 <= aum <= 50000:
            aum_pts = 10
        elif aum > 50000:
            aum_pts = 7   # capacity constraint concern for small/mid
        elif aum >= 500:
            aum_pts = 5
        else:
            aum_pts = 2   # too small — liquidity risk
        score += aum_pts
        breakdown["aum"] = {"value": aum, "points": aum_pts}

        return {
            "fund_key":  fund_key,
            "fund_name": f["name"],
            "score":     round(min(score, 100), 1),
            "breakdown": breakdown,
            "quality_tier": fund_key in QUALITY_TIER,
        }

    def score_all_funds(self) -> list[dict]:
        """Score all funds and return sorted list."""
        scores = [self.score_fund(k) for k in self.funds]
        return sorted(scores, key=lambda x: x.get("score", 0), reverse=True)

    # ─────────────────────────────────────────────
    # SIP RECOMMENDATION
    # ─────────────────────────────────────────────

    def recommend_sip(
        self,
        monthly_budget: float,
        risk_appetite: str = "moderate",    # conservative/moderate/aggressive
        time_horizon_years: int = 10,
        goal: str = "wealth_creation",      # wealth_creation/income/tax_saving
    ) -> dict[str, Any]:
        """
        Recommend a SIP portfolio based on operator goals.

        Allocation framework:
          Core (50-60%):     Large-cap / Flexi-cap
          Growth (25-35%):   Mid-cap / Small-cap
          Satellite (10-15%):Sectoral / Thematic / International
        """
        budget = Decimal(str(monthly_budget))
        recommendations: list[SIPRecommendation] = []

        if risk_appetite == "conservative":
            allocations = {
                "ICICI_BALANCED":  0.60,
                "MIRAE_LARGECAP":  0.30,
                "PPFAS_FLEXI":     0.10,
            }
        elif risk_appetite == "aggressive":
            allocations = {
                "PPFAS_FLEXI":      0.30,
                "HDFC_MIDCAP":      0.30,
                "NIPPON_SMALLCAP":  0.25,
                "QUANT_FLEXI":      0.15,
            }
        else:  # moderate (default)
            allocations = {
                "PPFAS_FLEXI":      0.40,
                "HDFC_MIDCAP":      0.35,
                "NIPPON_SMALLCAP":  0.25,
            }

        for fund_key, alloc_pct in allocations.items():
            f = self.funds.get(fund_key, {})
            amount = (budget * Decimal(str(alloc_pct))).quantize(Decimal("1"))
            # Round to nearest 500
            amount = Decimal(str(round(float(amount) / 500) * 500))

            recommendations.append(SIPRecommendation(
                fund_key=fund_key,
                fund_name=f.get("name", fund_key),
                category=f.get("category", ""),
                monthly_amount=amount,
                allocation_pct=alloc_pct * 100,
                risk_level=f.get("risk", "moderate"),
                reason=self._recommendation_reason(fund_key, f, alloc_pct),
                key_metrics={
                    "5y_returns": f.get("returns_5y"),
                    "alpha_5y":   f.get("alpha_5y"),
                    "sharpe":     f.get("sharpe"),
                    "expense":    f.get("expense"),
                    "manager_yrs":f.get("manager_yrs"),
                    "downside_capture": f.get("downside_capture"),
                },
            ))

        # Goal-based CAGR assumption
        assumed_cagr = {"conservative": 11, "moderate": 13, "aggressive": 15}
        cagr = assumed_cagr.get(risk_appetite, 13)
        future_value = self._future_value(float(budget), cagr, time_horizon_years)

        return {
            "recommendations": [
                {
                    "fund":        r.fund_name,
                    "category":    r.category,
                    "amount":      float(r.monthly_amount),
                    "allocation":  r.allocation_pct,
                    "risk":        r.risk_level,
                    "reason":      r.reason,
                    "metrics":     r.key_metrics,
                }
                for r in recommendations
            ],
            "total_monthly":    float(budget),
            "risk_appetite":    risk_appetite,
            "time_horizon_yrs": time_horizon_years,
            "goal":             goal,
            "assumed_cagr_pct": cagr,
            "projected_value":  round(future_value, 0),
            "step_up_plan":     (
                f"Increase SIP by 10% every April. "
                f"At 10% step-up with {cagr}% CAGR over {time_horizon_years} years: "
                f"projected corpus ₹{round(future_value * 1.45, 0):,.0f}"
            ),
        }

    # ─────────────────────────────────────────────
    # SCENARIO ENGINE
    # ─────────────────────────────────────────────

    def sip_scenario(self, scenario: str, **kwargs) -> dict[str, Any]:
        """
        SIP decision guidance for market scenarios.

        Scenarios:
          market_crash    — Nifty down 15%+ from peak
          all_time_high   — Nifty PE > 24x
          fund_underperform — 3yr below benchmark
          fund_manager_change — manager left
          need_money_soon — goal horizon < 3 years
        """
        handlers = {
            "market_crash":        self._scenario_crash,
            "all_time_high":       self._scenario_ath,
            "fund_underperform":   self._scenario_underperform,
            "fund_manager_change": self._scenario_manager,
            "need_money_soon":     self._scenario_short_horizon,
        }
        handler = handlers.get(scenario)
        if not handler:
            return {"error": f"Unknown scenario: {scenario}",
                    "valid_scenarios": list(handlers.keys())}
        return handler(**kwargs)

    def _scenario_crash(self, nifty_drop_pct: float = 25) -> dict:
        units_benefit = round(10000 / (100 - nifty_drop_pct) * 100, 2) - 100
        return {
            "scenario":   "market_crash",
            "action":     "CONTINUE SIP — Do NOT pause",
            "rationale": (
                f"Nifty is down {nifty_drop_pct:.0f}% from peak. "
                "Your SIP is now buying MORE units at a lower price. "
                f"Same ₹1,000 SIP buys {units_benefit:.1f}% more units than 3 months ago. "
                "This is the SIP machine working as intended."
            ),
            "historical_context": (
                "Investors who paused SIPs during 2008 (-52%), 2020 (-38%), 2022 (-18%) "
                "and resumed after recovery forfeited average 15-22% additional returns "
                "vs those who continued uninterrupted."
            ),
            "action_items": [
                "Do NOT pause your SIP",
                "If you have lump-sum available, consider adding 1-2× monthly SIP amount",
                "Review each fund's thesis — if thesis intact, do nothing",
                "Avoid checking portfolio daily — it will create panic",
            ],
        }

    def _scenario_ath(self, nifty_pe: float = 26) -> dict:
        return {
            "scenario": "all_time_high",
            "action":   "CONTINUE SIP — Consider pausing lump-sums",
            "rationale": (
                f"Nifty PE at {nifty_pe:.1f}x (above historical avg ~20x). "
                "Market is expensive but SIP automatically buys fewer units when prices are high. "
                "The SIP mechanism self-corrects for expensive markets."
            ),
            "action_items": [
                "Continue monthly SIP uninterrupted",
                "Pause any new lump-sum investments temporarily",
                "Consider switching satellite/sectoral allocation to liquid fund",
                "Do not exit equity funds — markets can stay expensive longer than expected",
            ],
        }

    def _scenario_underperform(
        self, fund_name: str = "Fund X",
        underperform_years: int = 3, underperform_pct: float = 3
    ) -> dict:
        return {
            "scenario": "fund_underperform",
            "action":   "INVESTIGATE before deciding",
            "rationale": (
                f"{fund_name} has underperformed benchmark by {underperform_pct:.1f}% "
                f"over {underperform_years} years. Key question: "
                "Is this fund-manager-specific or category-specific underperformance?"
            ),
            "decision_tree": {
                "Manager changed < 2 years ago": "EXIT — new manager unproven",
                "Category underperforming (e.g. all mid-caps down)": (
                    "HOLD — phases end, category will recover"
                ),
                "Fund underperforming its own category peers": "EXIT — switch to category leader",
                "No clear reason identified": "HOLD for 6 more months, then decide",
            },
            "action_items": [
                "Check if fund manager changed in last 2 years",
                "Compare vs category average (not just benchmark)",
                "If switching: invest in better-ranked fund in same category",
            ],
        }

    def _scenario_manager(self, fund_name: str = "Fund X") -> dict:
        return {
            "scenario": "fund_manager_change",
            "action":   "MONITOR for 6 months",
            "rationale": (
                f"Fund manager change at {fund_name}. "
                "Historical data: ~40% of manager changes lead to style drift "
                "within 12 months. The first 6 months of performance are critical."
            ),
            "action_items": [
                "Continue SIP for 6 months — do not panic-exit",
                "Track performance vs category monthly",
                "If underperforms category by >2% within 6 months → switch",
                "Research new manager's track record at previous fund",
            ],
        }

    def _scenario_short_horizon(self, years_to_goal: float = 2) -> dict:
        return {
            "scenario": "need_money_soon",
            "action":   "SWITCH OUT OF EQUITY immediately",
            "rationale": (
                f"With {years_to_goal:.0f} year(s) to goal, equity funds are inappropriate. "
                "Sequence-of-returns risk: a 30% market drop in year 1 of a 2-year horizon "
                "is unrecoverable before you need the money."
            ),
            "recommended_switch": {
                "Short-term debt funds":  "1-3 year horizon — 6-7% expected",
                "Arbitrage funds":        "< 1 year, tax-efficient, 5-6% expected",
                "Liquid funds":           "< 6 months, capital safe, 5-5.5% expected",
            },
            "action_items": [
                "Stop SIP into equity for this specific goal",
                "Move accumulated corpus to short-term debt / arbitrage",
                "Keep other long-term goals in equity — do not disturb",
            ],
        }

    # ─────────────────────────────────────────────
    # TAX OPTIMISER
    # ─────────────────────────────────────────────

    def tax_alert(
        self,
        holdings: list[dict],
        ltcg_used_so_far: float = 0,
        financial_year_end: str = "2026-03-31",
    ) -> dict[str, Any]:
        """
        Generate tax optimisation alerts.
        LTCG exemption: ₹1.25L per financial year (post Budget 2024-25).
        LTCG rate: 12.5% above exemption.
        STCG rate: 20%.
        """
        exemption = 125000.0
        remaining_exemption = max(0, exemption - ltcg_used_so_far)
        alerts = []

        for h in holdings:
            symbol    = h.get("fund", "?")
            purchase  = h.get("purchase_date", "")
            units     = h.get("units", 0)
            buy_nav   = h.get("buy_nav", 0)
            curr_nav  = h.get("current_nav", 0)
            gain      = (curr_nav - buy_nav) * units

            # Check if approaching LTCG eligibility (1 year holding)
            if purchase:
                from datetime import date
                try:
                    buy_date = date.fromisoformat(purchase)
                    today    = date.today()
                    days_held = (today - buy_date).days
                    days_to_ltcg = max(0, 366 - days_held)

                    if 0 < days_to_ltcg <= 30:
                        alerts.append({
                            "type":    "ltcg_soon",
                            "fund":    symbol,
                            "message": (
                                f"⏰ {symbol} becomes LTCG-eligible in {days_to_ltcg} days. "
                                "If you need to redeem, wait for LTCG to save 20%→12.5% tax."
                            ),
                            "days_to_ltcg": days_to_ltcg,
                        })

                    if gain > 0 and days_held >= 366 and gain <= remaining_exemption:
                        alerts.append({
                            "type":    "book_gains",
                            "fund":    symbol,
                            "message": (
                                f"✅ You can book ₹{gain:,.0f} LTCG from {symbol} "
                                f"TAX-FREE (within ₹{remaining_exemption:,.0f} exemption). "
                                "Consider booking before March 31 and reinvesting."
                            ),
                            "gain":    round(gain, 0),
                        })
                except Exception:
                    pass

        return {
            "ltcg_exemption":         exemption,
            "ltcg_used":              ltcg_used_so_far,
            "ltcg_remaining":         remaining_exemption,
            "alerts":                 alerts,
            "year_end":               financial_year_end,
            "tip": (
                "Book up to ₹1.25L LTCG tax-free each year and reinvest immediately. "
                "This resets your cost basis and eliminates the deferred tax liability."
            ),
        }

    # ─────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────

    def _future_value(
        self,
        monthly_sip: float,
        annual_cagr_pct: float,
        years: int,
    ) -> float:
        """Standard SIP future value formula."""
        r     = annual_cagr_pct / 100 / 12
        n     = years * 12
        if r == 0:
            return monthly_sip * n
        return monthly_sip * ((1 + r) ** n - 1) / r * (1 + r)

    def _recommendation_reason(self, key: str, f: dict, alloc: float) -> str:
        reasons = {
            "PPFAS_FLEXI":     "Core holding: consistent alpha, low turnover, global exposure",
            "MIRAE_LARGECAP":  "Stability anchor: large-cap with strong downside protection",
            "NIPPON_SMALLCAP": "Growth engine: top-ranked small cap with long manager tenure",
            "ICICI_BALANCED":  "Conservative core: balanced advantage with low volatility",
            "HDFC_MIDCAP":     "Mid-cap growth: veteran manager, consistent 14yr track record",
            "QUANT_FLEXI":     "Satellite: quant-driven high-alpha (high risk, small allocation)",
        }
        return reasons.get(key, f"{f.get('category','?')} — {alloc*100:.0f}% allocation")
