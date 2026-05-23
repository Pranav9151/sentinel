"""
sentinel/ui/dashboard.py  (Sprint 3)
=====================================
Project Sentinel — Main Streamlit Dashboard.

Sprint 3 additions:
  - All 7 screeners live in the Screeners tab
  - Trade Research Card display
  - All None formatting bugs fixed with safe helpers

Run with:
    streamlit run sentinel/ui/dashboard.py
"""

from __future__ import annotations

import logging
import math
import os
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sentinel.core.config import load_config, OperatorProfile
from sentinel.core.types import utc_now
from sentinel.data.kite_connector import KiteConnector
from sentinel.data.forex_connector import ForexConnector
from sentinel.data.historical_store import HistoricalStore
from sentinel.data.fundamental_store import FundamentalStore
from sentinel.data.market_data import MarketDataStore
from sentinel.data.mock_data import ALL_MOCK_STOCKS, MOCK_FOREX_PRICES, mock_ohlcv
from sentinel.indicators.technical import compute_all
from sentinel.ops.killswitch import is_kill_active, get_kill_state
from sentinel.ops.deployment_readiness import build_deployment_readiness_report
from sentinel.reports.morning_brief import MorningBrief
from sentinel.screeners.runner import ScreenerRunner
from sentinel.core.guardrails import GuardrailEngine
from sentinel.core.premortem import PreMortemJournal
from sentinel.data.mf_advisor import MFAdvisor
from sentinel.fo.covered_call import (
    CoveredCallCandidate,
    CoveredCallPlanner,
    EquityHolding,
    HedgeRejection,
)
from sentinel.fo.greeks_dashboard import OptionContract
from sentinel.lifecycle.lifecycle_gate import (
    StrategyLifecycleEvidence,
    StrategyLifecycleGate,
    StrategyLifecycleStage,
)
from sentinel.regime.hmm import classify_regime
from sentinel.research.sprint7_factory import build_sprint7_research_snapshot

logger = logging.getLogger(__name__)
MOCK_MODE = os.getenv("MOCK_MODE", "true").lower() == "true"


# ─────────────────────────────────────────────
# SAFE FORMAT HELPERS — fixes every None crash
# ─────────────────────────────────────────────

def _safe(v, default=0.0):
    if v is None:
        return default
    try:
        if isinstance(v, float) and math.isnan(v):
            return default
        return v
    except Exception:
        return default

def _f(v, fmt=".2f", fallback="—"):
    try:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return fallback
        return format(float(v), fmt)
    except Exception:
        return fallback

def _pct(v, decimals=2, fallback="—"):
    try:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return fallback
        return f"{float(v):+.{decimals}f}%"
    except Exception:
        return fallback

def _inr(v, fallback="—"):
    try:
        if v is None:
            return fallback
        return f"₹{float(v):,.2f}"
    except Exception:
        return fallback


# ─────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────

st.set_page_config(
    page_title="Project Sentinel",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
.main{background-color:#0e1117}
.kill-banner{background:#ff4b4b;color:#fff;padding:15px;border-radius:8px;
  font-weight:bold;font-size:18px;text-align:center;margin-bottom:20px}
.mock-banner{background:#1a3a1a;color:#00cc00;padding:8px 15px;
  border-radius:5px;font-size:13px;margin-bottom:10px}
.amber-banner{background:#3a3000;color:#ffcc00;padding:10px 15px;
  border-radius:5px;font-size:13px;margin:10px 0}
.def-banner{background:#3a1a00;color:#ff8800;padding:10px 15px;
  border-radius:5px;font-size:14px;font-weight:bold;margin:10px 0}
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────
# CACHED RESOURCES
# ─────────────────────────────────────────────

@st.cache_resource
def get_connectors():
    return KiteConnector(), ForexConnector()

@st.cache_resource
def get_stores():
    return HistoricalStore(), FundamentalStore(), MarketDataStore()

@st.cache_resource
def get_profile():
    try:
        return load_config()
    except Exception:
        return OperatorProfile()

@st.cache_data(ttl=300)
def fetch_ohlcv(symbol, days, timeframe, is_forex=False):
    store, _, _ = get_stores()
    kite, forex = get_connectors()
    if is_forex:
        return forex.get_forex_ohlcv(symbol, periods=days, timeframe=timeframe)
    bars = store.get_ohlcv(symbol, as_of=utc_now(), lookback_days=days)
    if not bars:
        bars = kite.get_historical(symbol, days=days, timeframe=timeframe)
    return bars

@st.cache_data(ttl=60)
def fetch_tick(symbol):
    kite, _ = get_connectors()
    return kite.get_live_tick(symbol)

@st.cache_data(ttl=600)
def fetch_fundamentals(symbol):
    _, fund, _ = get_stores()
    fund.ingest(symbol)
    return fund.compute_quality_score(symbol)

@st.cache_data(ttl=600)
def fetch_fii_dii():
    _, _, mkt = get_stores()
    mkt.ingest_fii_dii()
    return mkt.get_fii_dii(days=20), mkt.get_fii_trend()

@st.cache_data(ttl=1800)
def fetch_brief():
    _, _, mkt = get_stores()
    mkt.ingest_fii_dii()
    mkt.refresh_gsm_asm_list()
    return MorningBrief().generate()

@st.cache_data(ttl=300)
def fetch_macro():
    _, forex = get_connectors()
    return forex.get_macro_overlay()

@st.cache_data(ttl=600)
def fetch_strategy_factory_snapshot():
    return build_sprint7_research_snapshot(get_profile())

@st.cache_data(ttl=600)
def fetch_fo_hedge_candidates():
    profile = get_profile()
    planner = CoveredCallPlanner(float(profile.total_portfolio_value_inr))
    holdings = [
        EquityHolding("RELIANCE", quantity=250, average_price=2800, last_price=2950),
        EquityHolding("TCS", quantity=175, average_price=3600, last_price=3800),
    ]
    contracts = [
        OptionContract(
            symbol="RELIANCE24JUN3100CE",
            underlying="RELIANCE",
            option_type="call",
            strike=3100,
            expiry_days=30,
            lot_size=250,
            last_price=42,
            implied_vol_pct=22,
        ),
        OptionContract(
            symbol="TCS24JUN4000CE",
            underlying="TCS",
            option_type="call",
            strike=4000,
            expiry_days=30,
            lot_size=175,
            last_price=55,
            implied_vol_pct=20,
        ),
    ]
    by_symbol = {holding.symbol: holding for holding in holdings}
    return [
        planner.evaluate(by_symbol[contract.underlying], contract)
        for contract in contracts
        if contract.underlying in by_symbol
    ]

@st.cache_data(ttl=600)
def fetch_lifecycle_snapshot():
    snapshot = build_sprint7_research_snapshot(get_profile())
    metric = next(
        m for m in snapshot.strategy_metrics
        if m.strategy_id == "strategy2_value_momentum"
    )
    return StrategyLifecycleGate().evaluate(StrategyLifecycleEvidence(
        strategy_id=metric.strategy_id,
        current_stage=StrategyLifecycleStage.RESEARCH,
        oos_sharpe=metric.oos_sharpe,
        deflated_sharpe_ratio=0.95 if metric.research_gate_passed else 0.0,
        live_correlation_to_incumbent=snapshot.correlation_matrix
            .get("strategy1_momentum", {})
            .get("strategy2_value_momentum", 1.0),
        max_drawdown_pct=metric.oos_max_drawdown_pct,
    ))

@st.cache_data(ttl=600)
def fetch_regime_posterior():
    bars = mock_ohlcv("RELIANCE", days=120)
    closes = [float(bar["close"]) for bar in bars]
    returns = [
        (closes[i] - closes[i - 1]) / closes[i - 1]
        for i in range(1, len(closes))
        if closes[i - 1] > 0
    ]
    overlay = fetch_macro()
    dxy_change = _safe(getattr(overlay, "dxy_5d_change_pct", 0.0))
    india_vix = 18.0
    return classify_regime(returns, india_vix=india_vix, dxy_20d_change_pct=dxy_change)

@st.cache_data(ttl=600)
def fetch_deployment_readiness():
    return build_deployment_readiness_report(get_profile())

@st.cache_data(ttl=300)
def fetch_screeners():
    return ScreenerRunner().run_all()

def bars_to_df(bars):
    return pd.DataFrame([{
        "timestamp": b.timestamp,
        "open": float(b.open), "high": float(b.high),
        "low": float(b.low),  "close": float(b.close),
        "volume": b.volume,
    } for b in bars]).set_index("timestamp")


# ─────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────

def sidebar(profile):
    with st.sidebar:
        st.title("🛡️ Project Sentinel")
        st.caption(f"v5.0 Sprint 3  ·  {profile.trading_stage.upper()}")
        if is_kill_active():
            st.error(f"🔴 KILL SWITCH\n{get_kill_state()['reason']}")
        else:
            st.success("🟢 System Normal")
        if MOCK_MODE:
            st.markdown('<div class="mock-banner">📊 MOCK MODE</div>',
                        unsafe_allow_html=True)
        _, _, mkt = get_stores()
        if mkt.is_defensive_mode():
            st.markdown('<div class="def-banner">⚠️ DEFENSIVE MODE<br>VIX > 22</div>',
                        unsafe_allow_html=True)
        st.divider()
        page = st.radio("Navigation", [
            "📰 Morning Brief", "🎯 Screeners",
            "📈 Chart & Analysis", "📊 Fundamentals",
            "💰 FII / DII", "🌍 Forex & Macro",
            "Strategy Factory",
            "🧠 Guardrails & Journal", "💼 MF Advisor",
            "⚙️ Settings",
        ], label_visibility="collapsed")
        st.divider()
        st.metric("Capital", f"₹{float(profile.total_portfolio_value_inr):,.0f}")
        st.metric("Max Risk/Trade",
                  f"₹{float(profile.max_risk_per_trade_inr):,.0f}",
                  f"{profile.risk.max_risk_per_trade_pct}%")
        return page


# ─────────────────────────────────────────────
# PAGE: MORNING BRIEF
# ─────────────────────────────────────────────

def page_brief():
    st.title("📰 Morning Brief")
    with st.spinner("Generating..."):
        report = fetch_brief()
    s = report.get("sections", {})

    bias = s.get("bias", {}).get("bias", "NEUTRAL")
    icons = {"BULLISH":"🟢","CAUTIOUSLY_BULLISH":"🟡","NEUTRAL":"⚪",
             "CAUTIOUSLY_BEARISH":"🟠","BEARISH":"🔴"}
    st.markdown(f"## {icons.get(bias,'⚪')} Market Bias: **{bias}**")

    for flag in s.get("risk_flags", []):
        if "🔴" in flag or "⛔" in flag:
            st.error(flag)
        elif "🟠" in flag or "🟡" in flag:
            st.warning(flag)
        else:
            st.success(flag)

    st.divider()
    g    = s.get("global", {})
    us   = g.get("us_equity", {})
    comm = g.get("commodities", {})
    fx   = g.get("india_fx", {})
    rates= g.get("rates", {})

    c1, c2, c3 = st.columns(3)
    with c1:
        st.subheader("🌍 US Markets")
        if us.get("sp500_change_pct") is not None:
            st.metric("S&P 500", "—", _pct(us["sp500_change_pct"]))
        if us.get("nasdaq_change_pct") is not None:
            st.metric("Nasdaq",  "—", _pct(us["nasdaq_change_pct"]))
        if us.get("vix") is not None:
            st.metric("US VIX", _f(us["vix"],".1f"), us.get("vix_state",""))
    with c2:
        st.subheader("📦 Commodities")
        if comm.get("brent_usd"):
            chg = _safe(comm.get("brent_5d_change_pct"))
            st.metric("Brent", f"${_f(comm['brent_usd'],',.2f')}",
                      f"{chg:+.1f}% 5d")
        if comm.get("gold_usd"):
            chg = _safe(comm.get("gold_5d_change_pct"))
            st.metric("Gold",  f"${_f(comm['gold_usd'],',.0f')}",
                      f"{chg:+.1f}% 5d")
    with c3:
        st.subheader("💱 FX & Rates")
        if fx.get("usd_inr"):
            chg = _safe(fx.get("usd_inr_5d_change_pct"))
            st.metric("USD/INR", f"₹{_f(fx['usd_inr'],'.2f')}",
                      f"{chg:+.2f}% 5d")
        if rates.get("us_10y_yield"):
            chg = _safe(rates.get("us_10y_5d_change_bps"))
            st.metric("US 10Y", f"{_f(rates['us_10y_yield'],'.2f')}%",
                      f"{chg:+.0f}bps 5d")

    impact = g.get("india_impact", {}).get("summary", "")
    if impact:
        st.info(f"💵 DXY → India: {impact}")

    st.divider()
    c1, c2 = st.columns(2)
    fii = s.get("fii_dii", {})
    mi  = s.get("internals", {})
    with c1:
        st.subheader("💰 FII / DII")
        if fii.get("available"):
            fn = _safe(fii.get("fii_net_cr"))
            dn = _safe(fii.get("dii_net_cr"))
            st.metric("FII Net", f"₹{fn:+,.0f} Cr",
                      "Buying" if fn > 0 else "Selling")
            st.metric("DII Net", f"₹{dn:+,.0f} Cr",
                      "Buying" if dn > 0 else "Selling")
            tr  = fii.get("trend_20d", {})
            avg = _safe(tr.get("daily_avg_cr"))
            st.caption(f"20d: **{tr.get('trend','?')}** (₹{avg:+,.0f}Cr/day)")
    with c2:
        st.subheader("📊 Internals")
        if mi.get("nifty50_close"):
            st.metric("Nifty 50", f"{_safe(mi['nifty50_close']):,.0f}",
                      _pct(mi.get("nifty50_change_pct")))
        if mi.get("india_vix") is not None:
            st.metric("India VIX", f"{_safe(mi['india_vix']):.1f}",
                      mi.get("vix_label",""))
        if mi.get("advance_decline_ratio") is not None:
            st.metric("A/D Ratio",
                      f"{_safe(mi['advance_decline_ratio']):.2f}",
                      mi.get("ad_label",""))

    events = s.get("calendar", {}).get("high_impact_48h", [])
    if events:
        st.divider()
        st.subheader(f"📅 High-Impact Events (48h) — {len(events)}")
        for ev in events:
            imp  = ev.get("impact","")
            icon = "🔴" if imp == "CRITICAL" else "🟠"
            st.write(f"{icon} **[{imp}]** {ev.get('event_date','')} "
                     f"| {ev.get('currency','')} | {ev.get('event_name','')}")


# ─────────────────────────────────────────────
# PAGE: SCREENERS
# ─────────────────────────────────────────────

def _trade_card(cand: dict, rank: int):
    symbol = cand.get("symbol","?")
    sector = cand.get("sector","?")
    direction = cand.get("direction","BUY")
    score  = _safe(cand.get("conviction_score"))
    el     = _safe(cand.get("entry_low"))
    eh     = _safe(cand.get("entry_high"))
    sl     = _safe(cand.get("stop_loss"))
    t1     = _safe(cand.get("target_1"))
    rr     = _safe(cand.get("rr_ratio"))
    qty    = cand.get("suggested_qty","—")
    thesis = cand.get("thesis","")
    risks  = cand.get("risks",[])
    icon   = "🟢" if direction == "BUY" else "🔴"

    with st.expander(
        f"#{rank}  {icon} **{symbol}** — {sector}  |  "
        f"Score: {score:.0f}/100  |  R:R 1:{rr:.1f}",
        expanded=(rank == 1),
    ):
        c1,c2,c3,c4 = st.columns(4)
        c1.metric("Action",     f"{icon} {direction}")
        c2.metric("Entry Zone", f"₹{el:,.2f} – ₹{eh:,.2f}")
        c3.metric("Stop Loss",  f"₹{sl:,.2f}")
        c4.metric("Target 1",   f"₹{t1:,.2f}")
        c1,c2,c3,c4 = st.columns(4)
        c1.metric("R:R",  f"1 : {rr:.1f}")
        c2.metric("Qty",  str(qty))
        c3.metric("Score",f"{score:.0f}/100")
        c4.metric("Sector",sector)
        st.progress(int(min(score,100))/100,
                    text=f"Conviction: {score:.0f}/100")
        if thesis:
            st.markdown("**📋 Thesis:**")
            st.write(thesis)
        if risks:
            st.markdown("**⚠️ Risks:**")
            for r in risks:
                st.write(f"• {r}")

def page_screeners():
    st.title("🎯 Screeners — Sprint 3")
    st.caption("All 7 screeners running on mock data")

    c1, c2 = st.columns([1, 3])
    with c1:
        sel = st.radio("Select", [
            "S1 — Momentum Breakout",
            "S2 — Value + Reversal",
            "S3 — Sector Momentum",
            "S4 — Penny & Small Cap",
            "S5 — Smart Institutional",
            "S6 — MF Conviction",
            "S7 — Forex Opportunity",
            "🔄 Refresh All",
        ])

    key_map = {
        "S1 — Momentum Breakout":  "s1_momentum",
        "S2 — Value + Reversal":   "s2_value",
        "S3 — Sector Momentum":    "s3_sector",
        "S4 — Penny & Small Cap":  "s4_penny",
        "S5 — Smart Institutional":"s5_institutional",
        "S6 — MF Conviction":      "s6_mf",
        "S7 — Forex Opportunity":  "s7_forex",
    }

    with c2:
        if sel == "🔄 Refresh All":
            st.cache_data.clear()
            st.rerun()
            return

        with st.spinner("Running screener..."):
            results = fetch_screeners()

        key  = key_map.get(sel,"")
        data = results.get(key,{})

        if data.get("error"):
            st.error(f"Screener error: {data['error']}")
            return

        candidates = data.get("candidates",[])
        meta       = data.get("meta",{})

        st.caption(
            f"Ran: {meta.get('ran_at','?')[:16]} UTC  |  "
            f"Results: {len(candidates)}  |  "
            f"Universe: {meta.get('universe_size','?')}"
        )

        if "S4" in sel:
            st.warning(
                "⚠️ HIGH RISK — Small/Penny Caps. "
                "Max 0.5% portfolio per stock. Max 2% aggregate."
            )

        if not candidates:
            st.info("No candidates matching criteria.")
            return

        for i, cand in enumerate(candidates):
            _trade_card(cand, i+1)


# ─────────────────────────────────────────────
# PAGE: CHART
# ─────────────────────────────────────────────

def page_chart():
    st.title("📈 Chart & Technical Analysis")
    c1,c2,c3 = st.columns([2,1,1])
    with c1:
        symbol = st.selectbox("Symbol", sorted(ALL_MOCK_STOCKS.keys()))
    with c2:
        days = st.selectbox("Period",[30,90,180,365,730],index=3)
    with c3:
        tf = st.selectbox("Timeframe",["day","week"],index=0)

    with st.spinner(f"Loading {symbol}..."):
        bars = fetch_ohlcv(symbol, days, tf)
    if not bars:
        st.error(f"No data for {symbol}")
        return

    df   = bars_to_df(bars)
    tick = fetch_tick(symbol)
    ind  = compute_all(bars)

    prev  = df["close"].iloc[-2] if len(df)>1 else float(tick.ltp)
    chg   = float(tick.ltp)-float(prev)
    chgp  = chg/float(prev)*100 if prev else 0

    m1,m2,m3,m4,m5,m6 = st.columns(6)
    m1.metric("LTP",     f"₹{float(tick.ltp):,.2f}", _pct(chgp))
    m2.metric("RSI 14",  _f(_safe(ind.get("rsi_14")),".1f"))
    m3.metric("ATR 14",  f"₹{_safe(ind.get('atr_14')):.2f}")
    m4.metric("Trend",   ind.get("trend_direction","—"))
    m5.metric("52W High",f"₹{_safe(ind.get('high_52w')):,.2f}")
    m6.metric("52W Low", f"₹{_safe(ind.get('low_52w')):,.2f}")

    fig = make_subplots(rows=3,cols=1,shared_xaxes=True,
                        vertical_spacing=0.03,row_heights=[0.6,0.2,0.2])
    fig.add_trace(go.Candlestick(
        x=df.index,open=df["open"],high=df["high"],
        low=df["low"],close=df["close"],name="Price",
        increasing_line_color="#00cc00",decreasing_line_color="#ff4444",
    ),row=1,col=1)
    cl = df["close"]
    for p,col in [(9,"#ff9900"),(20,"#00aaff"),(50,"#ff44aa")]:
        fig.add_trace(go.Scatter(x=df.index,y=cl.ewm(span=p,adjust=False).mean(),
            name=f"EMA {p}",line=dict(color=col,width=1)),row=1,col=1)
    fig.add_trace(go.Scatter(x=df.index,y=cl.rolling(200).mean(),
        name="SMA 200",line=dict(color="#ffff00",width=1.5)),row=1,col=1)

    d=cl.diff()
    g=d.clip(lower=0).ewm(com=13,adjust=False).mean()
    loss=(-d.clip(upper=0)).ewm(com=13,adjust=False).mean()
    rsi_s=100-(100/(1+g/loss.replace(0,1e-10)))
    fig.add_trace(go.Scatter(x=df.index,y=rsi_s,name="RSI",
        line=dict(color="#aa88ff",width=1.5)),row=2,col=1)
    for lv,cc in [(70,"red"),(30,"green"),(50,"gray")]:
        fig.add_hline(y=lv,line_dash="dot",line_color=cc,opacity=0.5,row=2,col=1)

    e12=cl.ewm(span=12,adjust=False).mean()
    e26=cl.ewm(span=26,adjust=False).mean()
    macd=e12-e26
    sig=macd.ewm(span=9,adjust=False).mean()
    hist=macd-sig
    fig.add_trace(go.Scatter(x=df.index,y=macd,name="MACD",
        line=dict(color="#00ccff",width=1.5)),row=3,col=1)
    fig.add_trace(go.Scatter(x=df.index,y=sig,name="Signal",
        line=dict(color="#ff8800",width=1.5)),row=3,col=1)
    fig.add_trace(go.Bar(x=df.index,y=hist,name="Hist",
        marker_color=["#00cc00" if v>=0 else "#ff4444" for v in hist],
        opacity=0.6),row=3,col=1)

    fig.update_layout(template="plotly_dark",height=720,
                      xaxis_rangeslider_visible=False,
                      margin=dict(l=0,r=0,t=10,b=0))
    st.plotly_chart(fig,use_container_width=True)

    with st.expander("📋 Indicators"):
        tbl = {
            "RSI 14":    _f(_safe(ind.get("rsi_14")),".1f"),
            "RSI Zone":  ind.get("rsi_zone","—"),
            "MACD":      _f(_safe(ind.get("macd_macd")),".4f"),
            "ATR 14":    _f(_safe(ind.get("atr_14")),".2f"),
            "BB Upper":  _inr(_safe(ind.get("bb_upper"))),
            "BB Lower":  _inr(_safe(ind.get("bb_lower"))),
            "BB %B":     _f(_safe(ind.get("bb_pct_b")),".3f"),
            "Vol Ratio": f"{_safe(ind.get('vol_ratio')):.2f}x",
            "OBV Trend": ind.get("obv_trend","—"),
            "Trend":     ind.get("trend_direction","—"),
            "Strength":  ind.get("trend_strength","—"),
        }
        st.table(pd.DataFrame(tbl.items(), columns=["Indicator","Value"]))


# ─────────────────────────────────────────────
# PAGE: FUNDAMENTALS
# ─────────────────────────────────────────────

def page_fundamentals():
    st.title("📊 Fundamental Analysis")
    symbol = st.selectbox("Stock", sorted(ALL_MOCK_STOCKS.keys()))
    with st.spinner(f"Loading {symbol}..."):
        sd = fetch_fundamentals(symbol)
    if not sd or sd.get("quality_score") is None:
        st.warning("No data")
        return
    qs  = _safe(sd.get("quality_score"))
    vs  = _safe(sd.get("valuation_score"),5)
    raw = sd.get("raw_data",{})
    c1,c2,c3 = st.columns(3)
    c1.metric("Quality Score",f"{qs:.1f}/10",
              "Strong" if qs>=7 else "Moderate" if qs>=5 else "Weak")
    c2.metric("Valuation",f"{vs}/10",
              "Cheap" if vs>=7 else "Fair" if vs>=5 else "Expensive")
    c3.metric("PE",f"{_safe(raw.get('pe')):.1f}x",
              f"Sector {_safe(raw.get('sector_pe')):.1f}x")
    st.divider()
    m1,m2,m3,m4 = st.columns(4)
    roe=_safe(raw.get("roe"))
    de=_safe(raw.get("debt_equity"))
    prm=_safe(raw.get("promoter_holding"))
    plg=_safe(raw.get("pledging"))
    m1.metric("ROE",f"{roe:.1f}%","✅" if roe>15 else "⚠️")
    m2.metric("D/E",f"{de:.2f}x","✅ Low" if de<0.5 else "🔴 High" if de>1 else "⚠️")
    m3.metric("Promoter",f"{prm:.1f}%","✅" if prm>50 else "⚠️")
    m4.metric("Pledging",f"{plg:.1f}%","✅" if plg<5 else "🔴")
    st.divider()
    for comp,data in sd.get("breakdown",{}).items():
        c1,c2,c3 = st.columns([2,1,1])
        c1.write(comp.replace("_"," ").title())
        c2.write(f"{_safe(data.get('value')):.1f}")
        mx=data.get("max",1) or 1
        c3.progress(_safe(data.get("points"))/mx)
    _,_,mkt=get_stores()
    mkt.refresh_gsm_asm_list()
    if mkt.is_on_surveillance(symbol):
        st.error(f"🔴 {symbol} ON GSM/ASM — Hard rejection active")
    else:
        st.success(f"✅ {symbol} not on surveillance")


# ─────────────────────────────────────────────
# PAGE: FII / DII
# ─────────────────────────────────────────────

def page_fii():
    st.title("💰 FII / DII Flows")
    with st.spinner("Loading..."):
        flows, trend = fetch_fii_dii()
    icons = {"strong_buying":"🟢🟢","buying":"🟢","neutral":"⚪",
             "selling":"🔴","strong_selling":"🔴🔴"}
    c1,c2,c3 = st.columns(3)
    c1.metric("20d Trend",
              f"{icons.get(trend.get('trend','neutral'),'⚪')} {trend.get('trend','?').upper()}")
    c2.metric("Net 20d",  f"₹{_safe(trend.get('net_total_cr')):+,.0f} Cr")
    c3.metric("Daily Avg",f"₹{_safe(trend.get('daily_avg_cr')):+,.0f} Cr/day")
    if not flows:
        st.warning("No data")
        return
    df = pd.DataFrame(flows)
    df["date"]=pd.to_datetime(df["date"])
    df=df.sort_values("date")
    df["fii_net_cr"]=df["fii_net_cr"].fillna(0)
    df["dii_net_cr"]=df["dii_net_cr"].fillna(0)
    fig=go.Figure()
    fig.add_trace(go.Bar(x=df["date"],y=df["fii_net_cr"],name="FII Net",
        marker_color=["#00cc00" if v>=0 else "#ff4444" for v in df["fii_net_cr"]]))
    fig.add_trace(go.Bar(x=df["date"],y=df["dii_net_cr"],name="DII Net",
        marker_color="#4488ff",opacity=0.7))
    fig.update_layout(template="plotly_dark",height=400,barmode="group",
                      title="FII & DII Daily Net Flows (₹ Cr)")
    st.plotly_chart(fig,use_container_width=True)
    with st.expander("Raw Data"):
        st.dataframe(df[["date","fii_buy_cr","fii_sell_cr","fii_net_cr",
                          "dii_buy_cr","dii_sell_cr","dii_net_cr"]]
                     .sort_values("date",ascending=False),use_container_width=True)


# ─────────────────────────────────────────────
# PAGE: FOREX & MACRO
# ─────────────────────────────────────────────

def page_forex():
    st.title("🌍 Forex & Global Macro")
    st.markdown('<div class="amber-banner">⚠️ <b>ANALYSIS ONLY</b> — '
                'International pairs are research only. '
                'INR pairs executable on NSE Currency Derivatives.</div>',
                unsafe_allow_html=True)
    st.subheader("Live Rates")
    cols=st.columns(4)
    for i,(pair,price) in enumerate(list(MOCK_FOREX_PRICES.items())[:12]):
        with cols[i%4]:
            st.metric(pair,f"{price:,.4f}",
                      "✅ NSE" if pair.endswith("INR") else "📊")
    st.divider()
    overlay=fetch_macro()
    regime=overlay.dxy_regime.value if overlay.dxy_regime else "neutral"
    emoji={"strong_up":"🔴⬆️","up":"🟠⬆️","neutral":"⚪",
           "down":"🟢⬇️","strong_down":"🟢🟢⬇️"}.get(regime,"⚪")
    st.metric("DXY Regime",f"{emoji} {regime.upper().replace('_',' ')}")
    impacts={
        "strong_up":  {"IT/Pharma":"✅ POSITIVE","Banking":"🔴 CAUTION","Metals/Auto":"🔴 NEGATIVE"},
        "up":         {"IT":"🟡 SLIGHT +","Metals":"🟡 SLIGHT -"},
        "neutral":    {"All":"⚪ No significant FX impact"},
        "down":       {"Banking":"🟡 SLIGHT +","IT":"🟡 SLIGHT -"},
        "strong_down":{"Banking/Metals":"✅ POSITIVE","IT/Pharma":"🔴 NEGATIVE"},
    }
    for sec,imp in impacts.get(regime,{}).items():
        st.write(f"• **{sec}**: {imp}")
    st.divider()
    pair=st.selectbox("Pair Chart",
        ["EURUSD","GBPUSD","USDJPY","XAUUSD","USDINR","USDBRNT"])
    with st.spinner(f"Loading {pair}..."):
        _,forex=get_connectors()
        bars=forex.get_forex_ohlcv(pair,periods=200,timeframe="1day")
    if bars:
        df=bars_to_df(bars)
        cl=df["close"]
        fig=go.Figure()
        fig.add_trace(go.Candlestick(x=df.index,open=df["open"],high=df["high"],
            low=df["low"],close=df["close"],name=pair,
            increasing_line_color="#00cc00",decreasing_line_color="#ff4444"))
        for p,col in [(21,"#00aaff"),(55,"#ff44aa"),(200,"#ffff00")]:
            fig.add_trace(go.Scatter(x=df.index,
                y=cl.ewm(span=p,adjust=False).mean(),
                name=f"EMA {p}",line=dict(color=col,width=1)))
        fig.update_layout(template="plotly_dark",height=500,
                          xaxis_rangeslider_visible=False,
                          margin=dict(l=0,r=0,t=10,b=0))
        st.plotly_chart(fig,use_container_width=True)


# ─────────────────────────────────────────────
# PAGE: SETTINGS
# ─────────────────────────────────────────────

def page_strategy_factory():
    st.title("Strategy Factory")
    st.caption("Sprint 7 - research-only multi-strategy promotion view")

    with st.spinner("Building research snapshot..."):
        snapshot = fetch_strategy_factory_snapshot()

    if snapshot.live_approved:
        st.success("All promotion gates passed. Live expansion can be reviewed.")
    else:
        st.warning("Research-only. Live deployment remains blocked until all gates pass.")

    c1, c2, c3 = st.columns(3)
    c1.metric("Promotion Status", snapshot.promotion_status)
    c2.metric("Stage", snapshot.stage.upper())
    c3.metric("Blocking Gates", len(snapshot.live_blockers))
    st.caption(f"Allocation method: {snapshot.allocation_method}")

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "Strategies",
        "Allocation",
        "F&O Hedges",
        "Lifecycle",
        "Regime",
        "Promotion Memo",
    ])

    with tab1:
        rows = [
            {
                "Strategy": metric.display_name,
                "Status": metric.status,
                "OOS Sharpe": metric.oos_sharpe,
                "OOS Return %": metric.oos_total_return_pct,
                "Max DD %": metric.oos_max_drawdown_pct,
                "Trades": metric.oos_n_trades,
                "Ann Vol %": metric.annualized_vol_pct,
                "Research Gate": "PASS" if metric.research_gate_passed else "BLOCKED",
            }
            for metric in snapshot.strategy_metrics
        ]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with tab2:
        weights_df = pd.DataFrame([
            {"Strategy": sid, "Weight %": round(weight * 100, 2)}
            for sid, weight in snapshot.target_weights.items()
        ])
        if not weights_df.empty:
            c1, c2 = st.columns([1, 1])
            with c1:
                st.subheader("Research Target Weights")
                st.dataframe(weights_df, use_container_width=True, hide_index=True)
            with c2:
                fig = go.Figure(data=[go.Pie(
                    labels=weights_df["Strategy"],
                    values=weights_df["Weight %"],
                    hole=0.45,
                )])
                fig.update_layout(template="plotly_dark", height=320,
                                  margin=dict(l=0,r=0,t=10,b=0))
                st.plotly_chart(fig, use_container_width=True)

        ids = list(snapshot.correlation_matrix)
        if ids:
            matrix = [[snapshot.correlation_matrix[a][b] for b in ids] for a in ids]
            fig = go.Figure(data=go.Heatmap(
                z=matrix,
                x=ids,
                y=ids,
                zmin=-1,
                zmax=1,
                colorscale="RdBu",
            ))
            fig.update_layout(template="plotly_dark", height=360,
                              margin=dict(l=0,r=0,t=10,b=0))
            st.subheader("Correlation Matrix")
            st.plotly_chart(fig, use_container_width=True)

        if snapshot.high_correlation_pairs:
            st.warning("High-correlation pairs need review before promotion.")
            st.dataframe(pd.DataFrame(
                snapshot.high_correlation_pairs,
                columns=["Strategy A", "Strategy B", "Correlation"],
            ), use_container_width=True, hide_index=True)

    with tab3:
        evaluations = fetch_fo_hedge_candidates()
        candidates = [item for item in evaluations if isinstance(item, CoveredCallCandidate)]
        rejections = [item for item in evaluations if isinstance(item, HedgeRejection)]
        st.warning("Hedging-only research. Naked directional F&O is structurally blocked.")

        if candidates:
            rows = []
            for candidate in candidates:
                greeks = candidate.greeks_snapshot.greeks
                rows.append({
                    "Contract": candidate.contract.symbol,
                    "Underlying": candidate.holding.symbol,
                    "Lots": candidate.lots,
                    "Premium": candidate.premium_income,
                    "Exposure %": candidate.notional_exposure_pct,
                    "Delta": greeks.delta,
                    "Gamma": greeks.gamma,
                    "Theta/day": greeks.theta,
                    "Vega": greeks.vega,
                    "Rho": greeks.rho,
                    "Warnings": "; ".join(candidate.warnings),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.info("No covered-call candidates fit the current portfolio and exposure caps.")

        if rejections:
            st.subheader("Blocked Hedge Attempts")
            st.dataframe(pd.DataFrame([
                {"Contract": rejection.symbol, "Reason": rejection.reason}
                for rejection in rejections
            ]), use_container_width=True, hide_index=True)

    with tab4:
        lifecycle = fetch_lifecycle_snapshot()
        c1, c2, c3 = st.columns(3)
        c1.metric("Strategy", lifecycle.strategy_id)
        c2.metric("Current Stage", lifecycle.current_stage.value.upper())
        c3.metric("Recommendation", lifecycle.recommended_stage.value.upper())
        if lifecycle.can_promote:
            st.success("Lifecycle evidence supports a one-stage promotion.")
        else:
            st.warning("Lifecycle promotion is blocked.")

        if lifecycle.blockers:
            st.subheader("Lifecycle Blockers")
            for blocker in lifecycle.blockers:
                st.error(blocker)
        if lifecycle.warnings:
            st.subheader("Lifecycle Warnings")
            for warning in lifecycle.warnings:
                st.warning(warning)

    with tab5:
        posterior = fetch_regime_posterior()
        c1, c2, c3 = st.columns(3)
        c1.metric("Regime", posterior.state.value.replace("_", " ").upper())
        c2.metric("Confidence", f"{posterior.confidence * 100:.1f}%")
        c3.metric("Risk Multiplier", f"{posterior.recommended_risk_multiplier:.2f}x")
        if posterior.vix_defensive:
            st.warning("VIX defensive overlay is active.")
        st.caption(posterior.rationale)
        regime_df = pd.DataFrame([
            {"State": state.value, "Probability %": round(prob * 100, 2)}
            for state, prob in posterior.probabilities.items()
        ])
        st.dataframe(regime_df, use_container_width=True, hide_index=True)
        fig = go.Figure(data=[go.Bar(
            x=regime_df["State"],
            y=regime_df["Probability %"],
            marker_color=["#00cc88", "#ffaa00", "#ff4444"],
        )])
        fig.update_layout(template="plotly_dark", height=320,
                          margin=dict(l=0,r=0,t=10,b=0))
        st.plotly_chart(fig, use_container_width=True)

    with tab6:
        gate_rows = [
            {
                "Gate": gate.name,
                "Status": "PASS" if gate.passed else "BLOCKED",
                "Detail": gate.detail,
            }
            for gate in snapshot.gates
        ]
        st.dataframe(pd.DataFrame(gate_rows), use_container_width=True, hide_index=True)
        st.text_area("Promotion Memo", snapshot.promotion_memo, height=320)


def page_settings(profile):
    st.title("⚙️ Settings")
    c1,c2=st.columns(2)
    with c1:
        st.subheader("Operator Profile")
        st.json({"portfolio":f"₹{float(profile.total_portfolio_value_inr):,.0f}",
                 "stage":profile.trading_stage,
                 "max_risk":f"₹{float(profile.max_risk_per_trade_inr):,.0f}"})
    with c2:
        st.subheader("Sprint 6 Readiness")
        for b in profile.validate_sprint6_ready() or ["✅ All gates passed"]:
            st.warning(b) if "⚠️" in b or b!="✅ All gates passed" else st.success(b)
    st.divider()
    readiness = fetch_deployment_readiness()
    st.subheader("Production Delivery Readiness")
    if readiness.ready:
        st.success("All production readiness checks passed.")
    else:
        st.warning(f"{len(readiness.blockers)} readiness checks are blocking production.")
    st.dataframe(pd.DataFrame(readiness.as_dict()["checks"]),
                 use_container_width=True, hide_index=True)
    st.divider()
    ks=get_kill_state()
    if ks["active"]:
        st.error(f"🔴 KILL SWITCH ACTIVE — {ks['reason']}")
    else:
        st.success("🟢 Kill switch not active")
    if st.button("🧪 Test Kill Switch"):
        from sentinel.ops.killswitch import run_kill_switch_test
        with st.spinner("Testing..."):
            r=run_kill_switch_test()
        st.success(r["verdict"]) if r["passed"] else st.error(r["verdict"])
    st.divider()
    hist,_,_=get_stores()
    st.metric("Symbols with OHLCV",len(hist.get_available_symbols()))
    if st.button("🔄 Refresh All Data"):
        with st.spinner("Refreshing..."):
            hist.ingest_nifty500_batch()
            _,fund,mkt=get_stores()
            fund.ingest_nifty500()
            mkt.ingest_fii_dii()
            mkt.refresh_gsm_asm_list()
        st.cache_data.clear()
        st.success("Done!")



# ─────────────────────────────────────────────
# PAGE: GUARDRAILS & PRE-MORTEM JOURNAL
# ─────────────────────────────────────────────

def page_guardrails():
    st.title("🧠 Guardrails & Pre-Mortem Journal")
    st.caption("Sprint 4 — Behavioral protection layer")

    engine  = GuardrailEngine()
    journal = PreMortemJournal()
    summary = engine.get_dashboard_summary()

    # Override status
    st.subheader("🛡️ Three-Override Rule Status")
    st.info(summary["status"])
    c1, c2, c3 = st.columns(3)
    c1.metric("Overrides Used (30d)", summary["overrides_30d"])
    c2.metric("Remaining", summary["overrides_remaining"])
    c3.metric("Threshold", summary["threshold"])

    if summary["demotion_triggered"]:
        st.error("🔴 DEMOTED TO PAPER MODE — 3 overrides in 30 days reached.")

    # Quick guardrail check
    st.divider()
    st.subheader("🔍 Quick Trade Check")
    with st.form("guardrail_check"):
        sym  = st.text_input("Symbol", "RELIANCE")
        c1, c2 = st.columns(2)
        on_surv = c1.checkbox("On GSM/ASM list?", value=False)
        has_card= c2.checkbox("Has screener card?", value=True)
        c1, c2, c3 = st.columns(3)
        wins  = c1.number_input("Recent wins", 0, 20, 0)
        trades= c2.number_input("Trades this week", 0, 20, 0)
        prop_pct = c3.number_input("Proposed size %", 0.1, 10.0, 1.0)
        submitted = st.form_submit_button("Run Guardrail Check")

    if submitted:
        result = engine.check_trade(
            symbol=sym,
            is_on_surveillance=on_surv,
            has_screener_card=has_card,
            recent_wins=int(wins),
            trades_this_week=int(trades),
            proposed_position_pct=float(prop_pct),
            standard_position_pct=1.0,
        )
        if result["blocked"]:
            for b in result["hard_blocks"]:
                st.error(f"🔴 **BLOCKED** — {b['guardrail_name']}: {b['message']}")
        if result["has_warnings"]:
            for w in result["warnings"]:
                st.warning(f"🟡 **WARNING** — {w['guardrail_name']}: {w['message']}")
        if result["clear"]:
            st.success("✅ All guardrails passed. Trade is system-driven.")

    # Pre-mortem journal
    st.divider()
    st.subheader("📓 Pre-Mortem Journal")
    all_entries = journal.get_all()
    closed      = journal.get_closed()
    open_trades = journal.get_open()

    c1, c2, c3 = st.columns(3)
    c1.metric("Total Entries", len(all_entries))
    c2.metric("Open Trades",   len(open_trades))
    c3.metric("Closed Trades", len(closed))

    if closed:
        analytics = journal.get_analytics()
        st.subheader("📊 Trading Analytics")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Win Rate",    f"{_safe(analytics.get('win_rate_pct')):.1f}%")
        c2.metric("Avg Win",     f"{_safe(analytics.get('avg_win_pct')):+.1f}%")
        c3.metric("Avg Loss",    f"{_safe(analytics.get('avg_loss_pct')):+.1f}%")
        c4.metric("Expectancy",  f"{_safe(analytics.get('expectancy')):+.2f}%")
        insight = analytics.get("insight","")
        if insight:
            st.info(f"💡 {insight}")

    if all_entries:
        with st.expander("📋 Journal Entries", expanded=False):
            for e in reversed(all_entries[-10:]):
                outcome = e.get("outcome","open")
                icon    = "✅" if outcome=="win" else "❌" if outcome=="loss" else "⏳"
                st.write(f"{icon} **{e['symbol']}** | Score: {e.get('conviction_score',0):.0f} | "
                         f"Entry: ₹{e.get('entry_price',0):,.2f} | "
                         f"Outcome: {outcome.upper()} "
                         f"({e.get('pnl_pct',0) or 0:+.1f}%)")
    else:
        st.info("No journal entries yet. Entries are created when you record a trade with a pre-mortem.")


# ─────────────────────────────────────────────
# PAGE: MF ADVISOR
# ─────────────────────────────────────────────

def page_mf_advisor():
    st.title("💼 MF Advisor")
    st.caption("Sprint 4 — Mutual Fund advisory engine")

    advisor = MFAdvisor()
    tab1, tab2, tab3 = st.tabs(["SIP Recommendation", "Fund Scores", "Scenarios"])

    with tab1:
        st.subheader("📊 SIP Portfolio Recommendation")
        c1, c2, c3 = st.columns(3)
        budget   = c1.number_input("Monthly SIP (₹)", 500, 100000, 3000, step=500)
        risk     = c2.selectbox("Risk Appetite",
                                ["conservative","moderate","aggressive"], index=1)
        horizon  = c3.slider("Time Horizon (years)", 3, 30, 10)

        rec = advisor.recommend_sip(budget, risk, horizon)
        st.info(f"Projected corpus in {horizon}yr at {rec['assumed_cagr_pct']}% CAGR: "
                f"**₹{rec['projected_value']:,.0f}**")
        st.caption(rec["step_up_plan"])

        for r in rec["recommendations"]:
            with st.expander(f"**{r['fund']}** — ₹{r['amount']:,.0f}/month ({r['allocation']:.0f}%)"):
                c1, c2, c3, c4 = st.columns(4)
                m = r.get("metrics", {})
                c1.metric("5Y Returns",  f"{_safe(m.get('5y_returns')):.1f}%")
                c2.metric("Alpha 5Y",    f"{_safe(m.get('alpha_5y')):.1f}%")
                c3.metric("Sharpe",      f"{_safe(m.get('sharpe')):.2f}")
                c4.metric("Expense",     f"{_safe(m.get('expense')):.2f}%")
                st.write(f"**Why:** {r['reason']}")

    with tab2:
        st.subheader("⭐ Fund Quality Scores")
        scores = advisor.score_all_funds()
        for s in scores:
            c1, c2, c3 = st.columns([3, 1, 1])
            c1.write(f"**{s['fund_name']}**"
                     f"{'  ✅ Quality Tier' if s['quality_tier'] else ''}")
            c2.metric("Score", f"{s['score']:.0f}/100")
            c3.progress(int(s["score"]) / 100)

    with tab3:
        st.subheader("🎯 SIP Decision Scenarios")
        scenario = st.selectbox("What's happening?", [
            "market_crash", "all_time_high", "fund_underperform",
            "fund_manager_change", "need_money_soon",
        ])
        extra = {}
        if scenario == "market_crash":
            extra["nifty_drop_pct"] = st.slider("Nifty drop %", 10, 60, 25)
        elif scenario == "all_time_high":
            extra["nifty_pe"] = st.slider("Nifty PE", 18, 35, 26)
        elif scenario == "need_money_soon":
            extra["years_to_goal"] = st.slider("Years to goal", 1, 5, 2)

        result = advisor.sip_scenario(scenario, **extra)
        st.success(f"**Action:** {result['action']}")
        st.write(result.get("rationale",""))
        items = result.get("action_items", [])
        if items:
            st.subheader("✅ Action Items")
            for item in items:
                st.write(f"• {item}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    profile = get_profile()
    page    = sidebar(profile)
    if is_kill_active():
        ks=get_kill_state()
        st.markdown(f'<div class="kill-banner">🔴 KILL SWITCH ACTIVE — {ks["reason"]}</div>',
                    unsafe_allow_html=True)
    dispatch = {
        "📰 Morning Brief":       page_brief,
        "🎯 Screeners":           page_screeners,
        "📈 Chart & Analysis":    page_chart,
        "📊 Fundamentals":        page_fundamentals,
        "💰 FII / DII":           page_fii,
        "🌍 Forex & Macro":       page_forex,
        "Strategy Factory":        page_strategy_factory,
        "🧠 Guardrails & Journal":page_guardrails,
        "💼 MF Advisor":          page_mf_advisor,
        "⚙️ Settings":            lambda: page_settings(profile),
    }
    fn = dispatch.get(page)
    if fn:
        fn()

if __name__ == "__main__":
    main()
