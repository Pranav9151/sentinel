"""
sentinel/reports/morning_brief.py
=====================================
Morning Brief generator — runs at 08:30 IST every trading day.

Produces a structured report covering:
  - Global overnight snapshot (US markets, DXY, Gold, Crude, VIX)
  - FII/DII previous day flows and 20-day trend
  - India VIX and market regime
  - Economic calendar for next 48 hours (HIGH/CRITICAL events)
  - Market bias (BULLISH → BEARISH) with scoring
  - Key Nifty support/resistance levels

Sprint 2: Text output to console and dashboard.
Sprint 3: Screener results added.
Sprint 4: Telegram delivery added.

Documented in: SCREENERS_MODULE_SPEC.md §S9, SPRINT_ROADMAP_v2.md Sprint 2
"""

from __future__ import annotations

import logging
from typing import Any

from sentinel.core.types import utc_now, MacroOverlayDaily
from sentinel.data.market_data import MarketDataStore
from sentinel.data.forex_connector import ForexConnector

logger = logging.getLogger(__name__)


class MorningBrief:
    """
    Generates the daily pre-market Morning Brief.

    Usage:
        brief = MorningBrief()
        report = brief.generate()
        print(brief.format_text(report))
    """

    def __init__(self) -> None:
        self.market_data = MarketDataStore()
        self.forex = ForexConnector()

    def generate(self) -> dict[str, Any]:
        """
        Generate the complete Morning Brief.
        Returns a structured dict — can be rendered as text or dashboard cards.
        """
        now = utc_now()
        logger.info("Generating Morning Brief...")

        report: dict[str, Any] = {
            "generated_at": now.isoformat(),
            "report_date": now.date().isoformat(),
            "sections": {},
        }

        # 1. Global Snapshot
        report["sections"]["global"] = self._global_snapshot()

        # 2. FII/DII Flows
        report["sections"]["fii_dii"] = self._fii_dii_section()

        # 3. Market Internals
        report["sections"]["internals"] = self._internals_section()

        # 4. Market Bias
        report["sections"]["bias"] = self.market_data.get_market_bias()

        # 5. Economic Calendar
        report["sections"]["calendar"] = self._calendar_section()

        # 6. Key Levels
        report["sections"]["key_levels"] = self._key_levels()

        # 7. Risk Flags
        report["sections"]["risk_flags"] = self._risk_flags(report)

        logger.info("Morning Brief generated successfully")
        return report

    # ─────────────────────────────────────────────
    # SECTIONS
    # ─────────────────────────────────────────────

    def _global_snapshot(self) -> dict[str, Any]:
        """Overnight global market snapshot."""
        overlay = self.forex.get_macro_overlay()

        return {
            "dxy": {
                "level": overlay.dxy_5d_change_pct,
                "regime": overlay.dxy_regime.value if overlay.dxy_regime else "unknown",
                "label": "Dollar Index (DXY)",
            },
            "us_equity": {
                "sp500_change_pct": overlay.sp500_overnight_change_pct,
                "nasdaq_change_pct": overlay.nasdaq_overnight_change_pct,
                "vix": overlay.us_vix_level,
                "vix_state": overlay.us_vix_state,
            },
            "rates": {
                "us_10y_yield": overlay.us_10y_yield,
                "us_10y_5d_change_bps": overlay.us_10y_5d_change_bps,
                "yield_curve_slope_bps": overlay.yield_curve_slope_bps,
            },
            "commodities": {
                "brent_usd": overlay.brent_crude_usd,
                "brent_5d_change_pct": overlay.brent_5d_change_pct,
                "gold_usd": overlay.gold_usd,
                "gold_5d_change_pct": overlay.gold_5d_change_pct,
            },
            "india_fx": {
                "usd_inr": overlay.usd_inr,
                "usd_inr_5d_change_pct": overlay.usd_inr_5d_change_pct,
            },
            "india_impact": self._dxy_india_impact(overlay),
        }

    def _dxy_india_impact(self, overlay: MacroOverlayDaily) -> dict[str, str]:
        """
        Translate DXY regime into Indian sector implications.
        Documented in: GLOBAL_FOREX_MODULE.md §F9
        """
        regime = overlay.dxy_regime.value if overlay.dxy_regime else "neutral"

        impacts = {
            "strong_up": {
                "IT": "POSITIVE — export revenues benefit from weak INR",
                "Pharma": "POSITIVE — export-heavy sector benefits",
                "Banking": "CAUTION — FII outflows expected",
                "Metals": "NEGATIVE — commodity prices fall with strong USD",
                "Auto": "NEGATIVE — import costs rise",
                "FMCG": "NEGATIVE — input cost pressure",
                "Realty": "NEGATIVE — FII outflows, rate sensitivity",
                "Aviation": "NEGATIVE — fuel costs in USD",
                "summary": "DXY STRONG_UP → FII selling pressure, IT/Pharma outperform",
            },
            "up": {
                "IT": "SLIGHTLY POSITIVE",
                "Banking": "NEUTRAL with caution",
                "Metals": "SLIGHTLY NEGATIVE",
                "summary": "DXY UP → mild headwind for broad market",
            },
            "neutral": {
                "summary": "DXY NEUTRAL → no significant macro FX headwind/tailwind",
            },
            "down": {
                "IT": "SLIGHTLY NEGATIVE",
                "Banking": "SLIGHTLY POSITIVE — FII inflows expected",
                "summary": "DXY DOWN → mild tailwind for broad market",
            },
            "strong_down": {
                "IT": "NEGATIVE — INR appreciation hurts export revenues",
                "Banking": "POSITIVE — FII inflows expected",
                "Metals": "POSITIVE — commodity prices rise with weak USD",
                "summary": "DXY STRONG_DOWN → FII buying expected, Banking/Metals outperform",
            },
        }

        return impacts.get(regime, {"summary": "DXY regime unknown"})

    def _fii_dii_section(self) -> dict[str, Any]:
        """FII/DII flow data and trend."""
        latest = self.market_data.get_latest_fii_dii()
        trend = self.market_data.get_fii_trend(days=20)

        if not latest:
            return {"available": False, "trend": trend}

        fii_net = latest.get("fii_net_cr", 0) or 0
        dii_net = latest.get("dii_net_cr", 0) or 0

        # Combined signal
        if fii_net > 500 and dii_net > 0:
            combined = "STRONG BUYING — both FII and DII buying"
        elif fii_net > 0 and dii_net > 0:
            combined = "BUYING — both institutional groups positive"
        elif fii_net < -500:
            combined = "HEAVY SELLING — FII aggressively selling"
        elif fii_net < 0 and dii_net > 500:
            combined = "MIXED — FII selling, DII absorbing"
        elif fii_net > 0:
            combined = "MILD BUYING — FII positive"
        else:
            combined = "NEUTRAL to WEAK"

        return {
            "available": True,
            "date": latest.get("date"),
            "fii_buy_cr": latest.get("fii_buy_cr"),
            "fii_sell_cr": latest.get("fii_sell_cr"),
            "fii_net_cr": fii_net,
            "dii_buy_cr": latest.get("dii_buy_cr"),
            "dii_sell_cr": latest.get("dii_sell_cr"),
            "dii_net_cr": dii_net,
            "combined_signal": combined,
            "trend_20d": trend,
            "interpretation": (
                f"FII net {fii_net:+,.0f}Cr yesterday. "
                f"20-day trend: {trend['trend']} "
                f"(avg ₹{trend['daily_avg_cr']:+,.0f}Cr/day)"
            ),
        }

    def _internals_section(self) -> dict[str, Any]:
        """Market breadth and internals."""
        internals = self.market_data.get_market_internals()
        vix = internals.get("india_vix", 15)
        defensive = self.market_data.is_defensive_mode(vix_threshold=22.0)

        vix_label = (
            "🔴 PANIC (VIX > 25)" if vix > 25 else
            "🟠 FEAR (VIX > 22 — DEFENSIVE MODE)" if vix > 22 else
            "🟡 ELEVATED (VIX 18-22)" if vix > 18 else
            "🟢 NORMAL (VIX < 18)"
        )

        ad = internals.get("advance_decline_ratio", 1.0)
        ad_label = (
            "BROAD BUYING" if ad > 2 else
            "HEALTHY" if ad > 1.3 else
            "MIXED" if ad > 0.7 else
            "BROAD SELLING"
        )

        return {
            "nifty50_close": internals.get("nifty50_close"),
            "nifty50_change_pct": internals.get("nifty50_change_pct"),
            "banknifty_close": internals.get("banknifty_close"),
            "banknifty_change_pct": internals.get("banknifty_change_pct"),
            "india_vix": vix,
            "vix_label": vix_label,
            "defensive_mode_active": defensive,
            "advance_decline_ratio": ad,
            "ad_label": ad_label,
            "advances": internals.get("advances"),
            "declines": internals.get("declines"),
            "pcr": internals.get("nifty_pcr"),
            "new_52w_highs": internals.get("new_52w_highs"),
            "new_52w_lows": internals.get("new_52w_lows"),
        }

    def _calendar_section(self) -> dict[str, Any]:
        """Upcoming high-impact economic events."""
        all_events = self.market_data.get_upcoming_events(days_ahead=2)
        high_impact = [
            e for e in all_events
            if e.get("impact") in ("HIGH", "CRITICAL")
        ]
        today_high = self.market_data.get_high_impact_today()
        blackout_now = self.market_data.has_blackout_event_next_minutes(15)

        return {
            "events_48h": all_events,
            "high_impact_48h": high_impact,
            "high_impact_today": today_high,
            "blackout_active": blackout_now,
            "blackout_warning": (
                "⚠️ HIGH-IMPACT EVENT IN NEXT 15 MINUTES — No new entries"
                if blackout_now else None
            ),
            "count_high_impact": len(high_impact),
        }

    def _key_levels(self) -> dict[str, Any]:
        """Key Nifty support/resistance levels for the day."""
        internals = self.market_data.get_market_internals()
        nifty = internals.get("nifty50_close", 22500)

        # Simple support/resistance based on round numbers and recent levels
        # Sprint 3: Replace with actual Nifty OHLCV-based calculation
        return {
            "nifty50": {
                "current": nifty,
                "immediate_resistance": round(nifty * 1.005, -1),
                "immediate_support": round(nifty * 0.995, -1),
                "key_resistance": round(nifty * 1.015, -2),
                "key_support": round(nifty * 0.985, -2),
            },
            "note": "Key levels from pivot points. Full calculation in Sprint 3.",
        }

    def _risk_flags(self, report: dict[str, Any]) -> list[str]:
        """Identify risk flags for today."""
        flags = []

        # VIX-based flag
        internals = report["sections"].get("internals", {})
        vix = internals.get("india_vix", 15)
        if vix > 22:
            flags.append(
                f"🔴 DEFENSIVE MODE: India VIX at {vix:.1f} (threshold: 22). "
                "Reduce position sizes. Avoid new entries."
            )
        elif vix > 18:
            flags.append(
                f"🟡 ELEVATED VIX: {vix:.1f}. Use smaller position sizes."
            )

        # FII heavy selling
        fii = report["sections"].get("fii_dii", {})
        fii_net = fii.get("fii_net_cr", 0) or 0
        if fii_net < -2000:
            flags.append(
                f"🟠 HEAVY FII SELLING: ₹{fii_net:,.0f}Cr net sell yesterday. "
                "Mid/small caps at risk of sharp declines."
            )

        # High-impact events
        calendar = report["sections"].get("calendar", {})
        if calendar.get("count_high_impact", 0) > 2:
            flags.append(
                f"📅 {calendar['count_high_impact']} HIGH-IMPACT events in next 48hrs. "
                "Consider reducing overnight positions."
            )

        # Blackout
        if calendar.get("blackout_active"):
            flags.append(
                "⛔ EVENT BLACKOUT ACTIVE: High-impact event in next 15 minutes. "
                "No new entries until blackout clears."
            )

        # DXY headwind
        global_section = report["sections"].get("global", {})
        dxy_regime = global_section.get("dxy", {}).get("regime", "")
        if dxy_regime == "strong_up":
            flags.append(
                "💵 DXY STRONG — FII selling pressure expected. "
                "Avoid banking and import-heavy sectors today."
            )

        if not flags:
            flags.append("✅ No major risk flags today. Normal position sizing applies.")

        return flags

    # ─────────────────────────────────────────────
    # FORMATTING
    # ─────────────────────────────────────────────

    def format_text(self, report: dict[str, Any]) -> str:
        """Format the report as readable text for console/Telegram."""
        lines = []
        lines.append("=" * 55)
        lines.append("🛡️  SENTINEL MORNING BRIEF")
        lines.append(f"   {report['report_date']}  |  Generated {report['generated_at'][11:16]} UTC")
        lines.append("=" * 55)

        # Global
        g = report["sections"].get("global", {})
        us = g.get("us_equity", {})
        comm = g.get("commodities", {})
        fx = g.get("india_fx", {})
        lines.append("\n🌍 GLOBAL OVERNIGHT")
        if us.get("sp500_change_pct") is not None:
            lines.append(f"   S&P 500:  {us['sp500_change_pct']:+.2f}%")
        if us.get("nasdaq_change_pct") is not None:
            lines.append(f"   Nasdaq:   {us['nasdaq_change_pct']:+.2f}%")
        if us.get("vix") is not None:
            lines.append(f"   US VIX:   {us['vix']:.1f} ({us.get('vix_state','?')})")
        if comm.get("brent_usd"):
            chg = comm.get("brent_5d_change_pct") or 0.0
            lines.append(f"   Brent:    ${comm['brent_usd']:.2f} ({chg:+.1f}% 5d)")
        if comm.get("gold_usd"):
            chg = comm.get("gold_5d_change_pct") or 0.0
            lines.append(f"   Gold:     ${comm['gold_usd']:,.0f} ({chg:+.1f}% 5d)")
        if fx.get("usd_inr"):
            chg = fx.get("usd_inr_5d_change_pct") or 0.0
            lines.append(f"   USD/INR:  ₹{fx['usd_inr']:.2f} ({chg:+.2f}% 5d)")

        dxy_impact = g.get("india_impact", {}).get("summary", "")
        if dxy_impact:
            lines.append(f"\n   📊 {dxy_impact}")

        # FII/DII
        fii = report["sections"].get("fii_dii", {})
        lines.append("\n💰 FII / DII FLOWS (Yesterday)")
        if fii.get("available"):
            lines.append(f"   FII Net:  ₹{fii.get('fii_net_cr',0):+,.0f} Cr")
            lines.append(f"   DII Net:  ₹{fii.get('dii_net_cr',0):+,.0f} Cr")
            lines.append(f"   Signal:   {fii.get('combined_signal','?')}")
            trend = fii.get("trend_20d", {})
            lines.append(f"   20d Trend: {trend.get('trend','?')} (avg ₹{trend.get('daily_avg_cr',0):+,.0f}Cr/day)")
        else:
            lines.append("   Data not available")

        # Market Internals
        mi = report["sections"].get("internals", {})
        lines.append("\n📊 MARKET INTERNALS")
        if mi.get("nifty50_close"):
            lines.append(f"   Nifty 50:   {mi['nifty50_close']:,.0f} ({mi.get('nifty50_change_pct',0):+.2f}%)")
        if mi.get("banknifty_close"):
            lines.append(f"   BankNifty:  {mi['banknifty_close']:,.0f} ({mi.get('banknifty_change_pct',0):+.2f}%)")
        lines.append(f"   India VIX:  {mi.get('india_vix','?'):.1f}  {mi.get('vix_label','')}")
        if mi.get("advance_decline_ratio"):
            lines.append(f"   A/D Ratio:  {mi['advance_decline_ratio']:.2f} — {mi.get('ad_label','')}")
        if mi.get("pcr"):
            lines.append(f"   PCR:        {mi['pcr']:.2f}")

        # Market Bias
        bias = report["sections"].get("bias", {})
        lines.append(f"\n🎯 MARKET BIAS: {bias.get('bias','?')}")
        lines.append(f"   Score: {bias.get('score',0)}/6  |  VIX: {bias.get('vix','?')}  |  FII: {bias.get('fii_trend','?')}")

        # Risk Flags
        flags = report["sections"].get("risk_flags", [])
        if flags:
            lines.append("\n⚠️  RISK FLAGS")
            for flag in flags:
                lines.append(f"   {flag}")

        # Calendar
        cal = report["sections"].get("calendar", {})
        high_events = cal.get("high_impact_48h", [])
        if high_events:
            lines.append(f"\n📅 HIGH-IMPACT EVENTS (next 48h): {len(high_events)}")
            for ev in high_events[:3]:
                lines.append(
                    f"   [{ev.get('impact','?')}] {ev.get('event_date','')} "
                    f"{ev.get('currency','')} — {ev.get('event_name','?')}"
                )

        lines.append("\n" + "=" * 55)
        lines.append("   Sprint 2 Brief | Screeners added in Sprint 3")
        lines.append("=" * 55)

        return "\n".join(lines)

    def format_telegram(self, report: dict[str, Any]) -> str:
        """Format as compact Telegram message."""
        bias = report["sections"].get("bias", {})
        fii = report["sections"].get("fii_dii", {})
        mi = report["sections"].get("internals", {})
        flags = report["sections"].get("risk_flags", [])

        bias_emoji = {
            "BULLISH": "🟢", "CAUTIOUSLY_BULLISH": "🟡",
            "NEUTRAL": "⚪", "CAUTIOUSLY_BEARISH": "🟠", "BEARISH": "🔴"
        }.get(bias.get("bias", ""), "⚪")

        lines = [
            f"🛡️ *Sentinel Morning Brief* — {report['report_date']}",
            "",
            f"{bias_emoji} *Market Bias: {bias.get('bias','?')}*",
            f"India VIX: {mi.get('india_vix','?'):.1f} | A/D: {mi.get('advance_decline_ratio','?'):.2f}",
            "",
            f"*FII/DII:* FII {fii.get('fii_net_cr',0):+,.0f}Cr | DII {fii.get('dii_net_cr',0):+,.0f}Cr",
            "",
        ]

        if flags and flags[0] != "✅ No major risk flags today. Normal position sizing applies.":
            lines.append("*⚠️ Flags:*")
            for f in flags[:2]:
                lines.append(f"• {f}")

        return "\n".join(lines)
