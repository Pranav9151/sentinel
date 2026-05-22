"""
sentinel/ops/scheduler.py
==========================
APScheduler-based Job Scheduler — Sprint 5.

Runs all Sentinel screeners, reports, and data jobs
at their correct IST-timezone cron schedules.

Job schedule (per SCREENERS_MODULE_SPEC.md §S9):
  - 08:30 IST Mon-Fri    — Morning brief generation + Telegram
  - 09:00 IST Mon-Fri    — GSM/ASM list refresh (first)
  - 09:25 IST Mon-Fri    — S1 Momentum Breakout confirmed run
  - 11:30 IST Mon-Fri    — S7 Forex (mid-day H4 cycle)
  - 15:30 IST Mon-Fri    — S7 Forex (afternoon H4 cycle)
  - 15:45 IST Mon-Fri    — S3 Sector Momentum post-close
  - 16:00 IST Mon-Fri    — GSM/ASM list refresh (second)
  - 16:30 IST Mon-Fri    — S5 Smart Institutional post-close
  - 16:45 IST Mon-Fri    — FII/DII data ingest
  - 19:30 IST daily      — S7 Forex (evening H4 cycle — operator window)
  - 23:30 IST daily      — S7 Forex (late cycle — queued for next morning)
  - 18:00 IST Sunday     — S2 Value + Reversal weekly
  - 20:00 IST Sunday     — S4 Penny/Small Cap weekly
  - 10th of month 12:00  — S6 MF Conviction monthly

Design rules:
  - BackgroundScheduler — non-blocking, runs in a daemon thread
  - Asia/Kolkata timezone for all cron expressions
  - Job isolation: one job failure never crashes the scheduler
  - Kill-switch aware: jobs check is_kill_active() before running
  - MOCK_MODE: jobs log "would run" instead of actual execution
  - Graceful shutdown via stop()
  - job_missed policy: log warning, do not re-run automatically

Documented in: SPRINT_ROADMAP_v2.md §R7.2, SCREENERS_MODULE_SPEC.md §S0.2.2
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED, EVENT_JOB_EXECUTED

from sentinel.ops.killswitch import is_kill_active

logger = logging.getLogger(__name__)

MOCK_MODE = os.getenv("MOCK_MODE", "true").lower() == "true"
IST = ZoneInfo("Asia/Kolkata")


# ─────────────────────────────────────────────
# JOB WRAPPERS
# ─────────────────────────────────────────────

def _safe_job(name: str, fn: Callable, *args: Any, **kwargs: Any) -> None:
    """
    Wrap every scheduled job for:
      1. Kill-switch pre-check
      2. Exception isolation (one job failing ≠ scheduler crash)
      3. Consistent logging
    """
    if is_kill_active():
        logger.warning(f"[SCHEDULER] Job '{name}' skipped — kill switch active.")
        return
    try:
        logger.info(f"[SCHEDULER] Starting job: {name}")
        fn(*args, **kwargs)
        logger.info(f"[SCHEDULER] Completed job: {name}")
    except Exception as e:
        logger.error(f"[SCHEDULER] Job '{name}' FAILED: {e}", exc_info=True)


# ─────────────────────────────────────────────
# DEFAULT JOB IMPLEMENTATIONS (mock-aware)
# ─────────────────────────────────────────────

def _job_morning_brief() -> None:
    """Generate and send Morning Brief at 08:30 IST."""
    if MOCK_MODE:
        logger.info("[MOCK JOB] morning_brief: generating Morning Brief...")
        return
    from sentinel.reports.morning_brief import MorningBrief
    from sentinel.ops.telegram_bot import TelegramBot
    brief = MorningBrief()
    report = brief.generate()
    text = brief.format_text(report)
    bot = TelegramBot()
    bot.send_morning_brief(text)


def _job_s1_momentum() -> None:
    """Run S1 Momentum Breakout screener at 09:25 IST."""
    if MOCK_MODE:
        logger.info("[MOCK JOB] s1_momentum: running S1 screener...")
        return
    from sentinel.screeners.runner import ScreenerRunner
    runner = ScreenerRunner()
    result = runner.run_one("s1_momentum")
    logger.info(f"[JOB] S1 produced {len(result.get('candidates', []))} candidates")


def _job_s2_value() -> None:
    """Run S2 Value + Reversal screener (Sunday 18:00 IST)."""
    if MOCK_MODE:
        logger.info("[MOCK JOB] s2_value: running S2 screener (weekly)...")
        return
    from sentinel.screeners.runner import ScreenerRunner
    runner = ScreenerRunner()
    result = runner.run_one("s2_value")
    logger.info(f"[JOB] S2 produced {len(result.get('candidates', []))} candidates")


def _job_s3_sector() -> None:
    """Run S3 Sector Momentum screener at 15:45 IST."""
    if MOCK_MODE:
        logger.info("[MOCK JOB] s3_sector: running S3 sector screener...")
        return
    from sentinel.screeners.runner import ScreenerRunner
    runner = ScreenerRunner()
    result = runner.run_one("s3_sector")
    logger.info(f"[JOB] S3 produced {len(result.get('candidates', []))} candidates")


def _job_s4_penny() -> None:
    """Run S4 Penny/Small Cap screener (Sunday 20:00 IST)."""
    if MOCK_MODE:
        logger.info("[MOCK JOB] s4_penny: running S4 penny screener (weekly)...")
        return
    from sentinel.screeners.runner import ScreenerRunner
    runner = ScreenerRunner()
    result = runner.run_one("s4_penny")
    logger.info(f"[JOB] S4 produced {len(result.get('candidates', []))} candidates (max 5)")


def _job_s5_institutional() -> None:
    """Run S5 Smart Institutional screener at 16:30 IST."""
    if MOCK_MODE:
        logger.info("[MOCK JOB] s5_institutional: running S5 screener...")
        return
    from sentinel.screeners.runner import ScreenerRunner
    runner = ScreenerRunner()
    result = runner.run_one("s5_institutional")
    logger.info(f"[JOB] S5 produced {len(result.get('candidates', []))} candidates")


def _job_s6_mf() -> None:
    """Run S6 MF Conviction screener (10th of month, 12:00 IST)."""
    if MOCK_MODE:
        logger.info("[MOCK JOB] s6_mf: running S6 MF screener (monthly)...")
        return
    from sentinel.screeners.runner import ScreenerRunner
    runner = ScreenerRunner()
    result = runner.run_one("s6_mf")
    logger.info(f"[JOB] S6 produced {len(result.get('candidates', []))} fund candidates")


def _job_s7_forex() -> None:
    """Run S7 Forex Opportunity screener (H4 cycle)."""
    if MOCK_MODE:
        logger.info("[MOCK JOB] s7_forex: running S7 forex screener (H4)...")
        return
    from sentinel.screeners.runner import ScreenerRunner
    runner = ScreenerRunner()
    result = runner.run_one("s7_forex")
    logger.info(f"[JOB] S7 produced {len(result.get('candidates', []))} forex setups")


def _job_gsm_asm_refresh() -> None:
    """Refresh GSM/ASM surveillance list (twice daily)."""
    if MOCK_MODE:
        logger.info("[MOCK JOB] gsm_asm_refresh: refreshing surveillance list...")
        return
    from sentinel.data.market_data import MarketDataStore
    store = MarketDataStore()
    store.refresh_gsm_asm_list()
    logger.info("[JOB] GSM/ASM list refreshed")


def _job_fii_dii_ingest() -> None:
    """Ingest FII/DII flow data (daily post-close)."""
    if MOCK_MODE:
        logger.info("[MOCK JOB] fii_dii_ingest: ingesting FII/DII flow data...")
        return
    from sentinel.data.market_data import MarketDataStore
    store = MarketDataStore()
    data = store.get_fii_dii_data()
    logger.info(f"[JOB] FII/DII data ingested. FII net: {data.get('fii_net_cr', 'N/A')} Cr")


# ─────────────────────────────────────────────
# SCHEDULER
# ─────────────────────────────────────────────

class SentinelScheduler:
    """
    APScheduler-based job scheduler for Project Sentinel.

    Usage:
        scheduler = SentinelScheduler()
        scheduler.start()
        # ... runs in background ...
        scheduler.stop()

    All jobs run in Asia/Kolkata timezone.
    One job failure does NOT crash the scheduler.
    Kill-switch activation skips all jobs until reset.
    """

    def __init__(self, custom_jobs: Optional[dict[str, Callable]] = None) -> None:
        """
        Args:
            custom_jobs: Optional dict to override default job functions.
                         Keys: job_id strings (e.g. 's1_momentum')
                         Values: callables with no args
                         Used in tests to inject mock functions.
        """
        self._scheduler = BackgroundScheduler(
            timezone=IST,
            job_defaults={
                "coalesce": True,        # Run once if missed multiple times
                "max_instances": 1,      # Never run same job twice simultaneously
                "misfire_grace_time": 300,  # 5 min grace for misfires
            },
        )
        self._custom_jobs = custom_jobs or {}
        self._running = False

        # Wire APScheduler event listeners
        self._scheduler.add_listener(
            self._on_job_event,
            EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED,
        )

        # Build job registry
        self._register_all_jobs()

        logger.info(
            f"[SCHEDULER] Initialised. MOCK_MODE={MOCK_MODE}. "
            f"Jobs registered: {len(self._scheduler.get_jobs())}"
        )

    def _resolve_job(self, job_id: str, default_fn: Callable) -> Callable:
        """Return custom job function if injected, else the default."""
        fn = self._custom_jobs.get(job_id, default_fn)
        return lambda: _safe_job(job_id, fn)

    def _register_all_jobs(self) -> None:
        """Register all Sentinel jobs with their IST cron schedules."""
        jobs = [
            # ── Daily Mon-Fri jobs ──────────────────────────────────────────
            {
                "id": "morning_brief",
                "fn": _job_morning_brief,
                "trigger": CronTrigger(
                    day_of_week="mon-fri", hour=8, minute=30, timezone=IST
                ),
                "name": "Morning Brief (08:30 IST)",
            },
            {
                "id": "gsm_asm_morning",
                "fn": _job_gsm_asm_refresh,
                "trigger": CronTrigger(
                    day_of_week="mon-fri", hour=9, minute=0, timezone=IST
                ),
                "name": "GSM/ASM Refresh (09:00 IST)",
            },
            {
                "id": "s1_momentum",
                "fn": _job_s1_momentum,
                "trigger": CronTrigger(
                    day_of_week="mon-fri", hour=9, minute=25, timezone=IST
                ),
                "name": "S1 Momentum Breakout (09:25 IST)",
            },
            {
                "id": "s7_forex_midday",
                "fn": _job_s7_forex,
                "trigger": CronTrigger(
                    day_of_week="mon-fri", hour=11, minute=30, timezone=IST
                ),
                "name": "S7 Forex H4 Mid-day (11:30 IST)",
            },
            {
                "id": "s7_forex_afternoon",
                "fn": _job_s7_forex,
                "trigger": CronTrigger(
                    day_of_week="mon-fri", hour=15, minute=30, timezone=IST
                ),
                "name": "S7 Forex H4 Afternoon (15:30 IST)",
            },
            {
                "id": "s3_sector",
                "fn": _job_s3_sector,
                "trigger": CronTrigger(
                    day_of_week="mon-fri", hour=15, minute=45, timezone=IST
                ),
                "name": "S3 Sector Momentum (15:45 IST)",
            },
            {
                "id": "gsm_asm_evening",
                "fn": _job_gsm_asm_refresh,
                "trigger": CronTrigger(
                    day_of_week="mon-fri", hour=16, minute=0, timezone=IST
                ),
                "name": "GSM/ASM Refresh (16:00 IST)",
            },
            {
                "id": "s5_institutional",
                "fn": _job_s5_institutional,
                "trigger": CronTrigger(
                    day_of_week="mon-fri", hour=16, minute=30, timezone=IST
                ),
                "name": "S5 Smart Institutional (16:30 IST)",
            },
            {
                "id": "fii_dii_ingest",
                "fn": _job_fii_dii_ingest,
                "trigger": CronTrigger(
                    day_of_week="mon-fri", hour=16, minute=45, timezone=IST
                ),
                "name": "FII/DII Data Ingest (16:45 IST)",
            },
            # ── Daily all-days jobs ─────────────────────────────────────────
            {
                "id": "s7_forex_evening",
                "fn": _job_s7_forex,
                "trigger": CronTrigger(hour=19, minute=30, timezone=IST),
                "name": "S7 Forex H4 Evening (19:30 IST)",
            },
            {
                "id": "s7_forex_late",
                "fn": _job_s7_forex,
                "trigger": CronTrigger(hour=23, minute=30, timezone=IST),
                "name": "S7 Forex H4 Late (23:30 IST)",
            },
            # ── Weekly jobs (Sunday) ────────────────────────────────────────
            {
                "id": "s2_value",
                "fn": _job_s2_value,
                "trigger": CronTrigger(
                    day_of_week="sun", hour=18, minute=0, timezone=IST
                ),
                "name": "S2 Value+Reversal (Sunday 18:00 IST)",
            },
            {
                "id": "s4_penny",
                "fn": _job_s4_penny,
                "trigger": CronTrigger(
                    day_of_week="sun", hour=20, minute=0, timezone=IST
                ),
                "name": "S4 Penny/Small Cap (Sunday 20:00 IST)",
            },
            # ── Monthly job (10th of month) ─────────────────────────────────
            {
                "id": "s6_mf",
                "fn": _job_s6_mf,
                "trigger": CronTrigger(day=10, hour=12, minute=0, timezone=IST),
                "name": "S6 MF Conviction (10th, 12:00 IST)",
            },
        ]

        for job_spec in jobs:
            self._scheduler.add_job(
                func=self._resolve_job(job_spec["id"], job_spec["fn"]),
                trigger=job_spec["trigger"],
                id=job_spec["id"],
                name=job_spec["name"],
                replace_existing=True,
            )

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background scheduler. Non-blocking."""
        if self._running:
            logger.warning("[SCHEDULER] Already running — ignoring start().")
            return
        self._scheduler.start()
        self._running = True
        logger.info(f"[SCHEDULER] Started. {len(self._scheduler.get_jobs())} jobs scheduled.")

    def stop(self) -> None:
        """Stop the scheduler gracefully."""
        if not self._running:
            return
        self._scheduler.shutdown(wait=False)
        self._running = False
        logger.info("[SCHEDULER] Stopped.")

    def is_running(self) -> bool:
        """Return True if scheduler is actively running."""
        return self._running and self._scheduler.running

    # ── Inspection ────────────────────────────────────────────────────────────

    def get_job_list(self) -> list[dict[str, Any]]:
        """Return a summary list of all registered jobs.

        Note: next_run is only available after the scheduler is started.
        Before start(), jobs are in 'pending' state and next_run_time is None.
        """
        jobs = []
        for job in self._scheduler.get_jobs():
            # next_run_time only exists on the object after scheduler.start()
            # Use getattr with None default for pending (pre-start) jobs
            next_run_time = getattr(job, "next_run_time", None)
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run": next_run_time.isoformat() if next_run_time else None,
            })
        return jobs

    def run_job_now(self, job_id: str) -> bool:
        """
        Manually trigger a job immediately (for testing / dashboard button).
        Returns True if job was found and executed, False otherwise.
        """
        job = self._scheduler.get_job(job_id)
        if not job:
            logger.warning(f"[SCHEDULER] Job '{job_id}' not found.")
            return False
        try:
            job.func()
            return True
        except Exception as e:
            logger.error(f"[SCHEDULER] Manual run of '{job_id}' failed: {e}")
            return False

    # ── Event listener ────────────────────────────────────────────────────────

    def _on_job_event(self, event: Any) -> None:
        """Handle APScheduler job events for logging."""
        if hasattr(event, "exception") and event.exception:
            logger.error(
                f"[SCHEDULER] Job '{event.job_id}' raised an exception: {event.exception}"
            )
        elif hasattr(event, "code"):
            if event.code == EVENT_JOB_MISSED:
                logger.warning(f"[SCHEDULER] Job '{event.job_id}' was missed.")
