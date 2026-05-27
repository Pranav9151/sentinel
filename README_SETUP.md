# Project Sentinel v5.0 - Operator Handoff

Project Sentinel is an AI-powered Trading Research Assistant for traders and
investors. It helps analyze stocks, mutual funds, ETFs, forex, commodities,
sectors, news, institutional activity, fundamentals, technicals, option-risk
profiles, and portfolio risk before decisions are made.

Sentinel does not execute trades automatically. It provides structured trade
plans, investment research, entry/exit zones, scenario analysis, option Greeks,
covered-call reviews, data-quality gates, and risk-management insights so the
user can make informed real-time trading decisions and execute manually.

The current codebase is complete through the Sprint 8 research-factory and
production-grade modules that can be built locally without fabricating real
live-trading evidence.

## Current Status

Code-side delivery is complete for:

- React AI research assistant console with Python JSON API
- Legacy Streamlit dashboard and settings
- Mock/live connector boundary
- Historical, fundamental, market, forex, and MF data modules
- Technical indicators and screeners
- Strategy 1 momentum research and backtest
- Strategy 2 value-momentum research
- Strategy 3 pairs mean-reversion research
- Paper trading and live-stage router
- Kill switch with HTTP secret validation
- Guardrails and pre-mortem journal
- Sprint 7 Strategy Factory
- HRP allocation for 3+ strategies
- Hedging-only F&O covered-call planner with Greeks
- Strategy lifecycle gate
- Regime posterior classifier
- Append-only audit log with hash-chain verification
- Production delivery readiness report

Live-production readiness is intentionally blocked until real operator evidence
exists. Do not bypass these gates in code.

## Quick Start

Run commands from the repository root:

```powershell
cd C:\Projects\sentinel_project
.\venv\Scripts\Activate.ps1
```

Launch the React dashboard:

```powershell
.\launch_react_dashboard.bat
```

Open:

```text
http://127.0.0.1:8765
```

The React UI and local Python API are served from the same process in this
mode. During frontend-only development, you can still run Vite from
`frontend/` and it will proxy `/api` to `http://127.0.0.1:8765`.

For a production-style static build:

```powershell
cd frontend
npm install
npm run build
cd ..
venv\Scripts\python.exe -m sentinel.ui.react_api
```

Open:

```text
http://127.0.0.1:8765
```

Run verification:

```powershell
venv\Scripts\ruff.exe check conftest.py sentinel --no-cache
venv\Scripts\pytest.exe sentinel\tests -q
```

The last verified full-suite result was:

```text
341 passed
```

## Dashboard Pages

- Morning Brief
- Screeners
- Chart & Analysis
- Fundamentals
- FII / DII
- Forex & Macro
- Strategy Factory
- Guardrails & Journal
- MF Advisor
- Settings

The Strategy Factory page includes:

- Strategy 1, Strategy 2, and Strategy 3 research metrics
- HRP or inverse-variance allocation method
- Correlation matrix
- F&O Hedges tab with Greeks
- Lifecycle tab
- Regime posterior tab
- Promotion memo

The Settings page includes:

- Sprint 6 readiness
- Production Delivery Readiness
- Kill switch status and test
- Data refresh controls

The legacy Streamlit dashboard remains available during the React transition:

```powershell
streamlit run sentinel/ui/dashboard.py
```

## Production Readiness

Run:

```powershell
venv\Scripts\python.exe -c "from sentinel.ops.deployment_readiness import build_deployment_readiness_report; r=build_deployment_readiness_report(); print(r.as_dict())"
```

Expected current result: `ready=False`.

Known blockers in the current repo/config:

- `trading_stage` is `paper`
- emergency fund confirmed is 2 months, but live requires at least 6
- Section 7.6 sign-off hash is blank
- `KILLSWITCH_SECRET` must be configured and not use the default
- no auditable 90 clean live-trading days exist yet
- Strategy Factory status is `RESEARCH_ONLY`

These are not missing code. They are real-world readiness requirements.

## Environment

Create `.env` from the example if needed:

```powershell
copy .env.example .env
```

For local development, keep:

```text
MOCK_MODE=true
```

Before any live market operation:

- Configure real broker/API credentials in `.env`
- Configure a non-default `KILLSWITCH_SECRET`
- Keep `.env` out of git
- Confirm the readiness report

## Git Workflow

Generated files are ignored:

- virtual environments
- caches
- SQLite DB/WAL/SHM files
- logs
- local kill-switch state
- test temp folders

Commit a verified checkpoint:

```powershell
git add .
git commit -m "Complete Sentinel production-grade research delivery"
git push
```

## Safety Rules

- LLMs narrate decisions; they never generate trading signals.
- Naked directional weekly options are not implemented.
- F&O is hedging-only.
- Live execution remains blocked unless stage, sign-off, kill-switch, evidence,
  and readiness checks pass.
- Do not change `trading_stage` manually to bypass gates.

## Recommended Daily Workflow

```text
07:00 IST - Open dashboard and read Morning Brief
07:30 IST - Review screeners and Strategy Factory
09:15 IST - Paper/live actions only if readiness stage permits
15:45 IST - Review post-market state
19:00 IST - Journal, guardrails, and weekly/monthly reports
```

## Final Operator Note

The codebase is ready for research/paper operation and further evidence
collection. It is not marked live-production ready until the readiness report
passes with real-world evidence.
