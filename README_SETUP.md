# Project Sentinel — Windows Setup Guide

Complete step-by-step guide to get the system running on Windows.

---

## STEP 1 — Project folder (already done ✅)

You should have:
```
C:\Projects\sentinel\
├── venv\
├── config.yaml
├── .env.example
├── requirements.txt
├── sentinel\
│   ├── core\
│   ├── data\
│   ├── indicators\
│   ├── ops\
│   ├── screeners\
│   ├── strategies\
│   ├── tests\
│   └── ui\
```

---

## STEP 2 — Copy the .env file

In PowerShell:
```powershell
cd C:\Projects\sentinel
copy .env.example .env
```

Open `.env` in VS Code. You will see all API keys listed.
**Leave them all blank for now** — `MOCK_MODE=true` means no real keys needed.

The only thing to verify: `MOCK_MODE=true` is set.

---

## STEP 3 — Activate virtual environment

Every time you open a new PowerShell window, run this first:
```powershell
cd C:\Projects\sentinel
.\venv\Scripts\Activate.ps1
```

You should see `(venv)` at the start of your prompt.

---

## STEP 4 — Install dependencies

```powershell
pip install -r requirements.txt
```

This installs everything. Takes 2-5 minutes first time.

If you see any errors, run this to fix common issues:
```powershell
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
```

---

## STEP 5 — Install TA-Lib (technical indicators)

TA-Lib requires a pre-compiled Windows binary.

1. Go to: https://github.com/cgohlke/talib-build/releases
2. Find the latest release
3. Download the file matching your Python version:
   - For Python 3.12: `TA_Lib-0.4.xx-cp312-cp312-win_amd64.whl`
4. Install it:
```powershell
# Navigate to where you downloaded the file (e.g. Downloads)
cd C:\Users\vibho\Downloads
pip install TA_Lib-0.4.xx-cp312-cp312-win_amd64.whl
```

If you cannot install TA-Lib right now, that is okay — the system
uses the `ta` library as a fallback. Full TA-Lib is added in Sprint 2.

---

## STEP 6 — Run the tests

This verifies everything is working correctly:
```powershell
cd C:\Projects\sentinel
pytest sentinel/tests/test_sprint1.py -v
```

Expected output:
```
test_sprint1.py::TestTypeSeparation::test_analysis_signal_has_no_create_order_method PASSED
test_sprint1.py::TestTypeSeparation::test_execution_signal_raises_for_ineligible_instrument PASSED
test_sprint1.py::TestDatetimeSafety::test_utc_now_is_timezone_aware PASSED
...
✅ ALL SPRINT 1 GATES PASSED — Ready for Sprint 2
```

---

## STEP 7 — Launch the dashboard

```powershell
cd C:\Projects\sentinel
streamlit run sentinel/ui/dashboard.py
```

Your browser should automatically open at:
```
http://localhost:8501
```

You should see:
- The Sentinel dashboard with sidebar navigation
- A green "MOCK MODE" banner at the top
- Chart page showing NSE stocks with live mock prices and indicators
- Forex page with global rates

**This is Sprint 1 complete.**

---

## STEP 8 — Run the kill switch test

In the dashboard:
1. Click "⚙️ Settings" in the sidebar
2. Click "🧪 Run Kill Switch Test"
3. You should see: `✅ PASSED — Kill switch flattened 5 positions in X.XXXs (< 5s limit)`

This is the Sprint 1 acceptance gate for the kill switch.

---

## COMMON ISSUES

### Issue: "execution policy" error when activating venv
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### Issue: `ModuleNotFoundError: No module named 'sentinel'`
Make sure you are running commands from `C:\Projects\sentinel\`:
```powershell
cd C:\Projects\sentinel
# Then run streamlit or pytest
```

### Issue: `config.yaml not found`
You are running from the wrong folder. Always run from `C:\Projects\sentinel\`.

### Issue: Port 8501 already in use
```powershell
streamlit run sentinel/ui/dashboard.py --server.port 8502
```

### Issue: Streamlit opens but shows errors
Check the PowerShell terminal for error messages.
Most common cause: missing package. Run `pip install -r requirements.txt` again.

---

## WHAT MOCK MODE MEANS

When `MOCK_MODE=true`:
- **All API calls return simulated data** — no internet needed
- **No API keys required** — build the entire system for free
- **Data is realistic** — prices, volumes, indicators all look real
- **All logic works the same** — tests pass, dashboard shows charts

When you are ready for real data:
1. Get your API keys (see `.env.example` for where to get each one)
2. Add them to `.env`
3. Set `MOCK_MODE=false`
4. Restart the dashboard

**The code does not change at all. Only the `.env` file changes.**

---

## SPRINT PROGRESS

| Sprint | Status | What you get |
|--------|--------|--------------|
| Sprint 1 | 🚀 **YOU ARE HERE** | Dashboard + live mock prices + kill switch |
| Sprint 2 | 📋 Next | Historical data + fundamental data + morning brief |
| Sprint 3 | 📋 Planned | 7 screeners + Strategy 1 + Trade Cards |
| Sprint 4 | 📋 Planned | Behavioral guardrails + MF module + Forex analysis |
| Sprint 5 | 📋 Planned | Paper trading + full validation |
| Sprint 6 | 📋 Planned | Real money (carefully) |

---

## DAILY WORKFLOW (once all sprints complete)

```
07:00 IST  — Open dashboard, read Morning Brief
07:30 IST  — Review S1 Momentum Breakout screener cards
09:15 IST  — Execute any morning setups (manual)
19:00 IST  — Review post-market screener output
19:30 IST  — Review S7 Forex setups for London-NY overlap
22:30 IST  — Close positions or set alerts for tomorrow
```

---

*Project Sentinel v5.0 — Build phase started*
