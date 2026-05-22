"""
sentinel/ops/telegram_bot.py
=============================
Telegram Alert Bot — Sprint 5.

Delivers alerts to the operator's Telegram chat.

In MOCK_MODE=true (default during build phase):
  - No real Telegram token required
  - All messages logged at INFO level instead of sent
  - TelegramBot() initialises successfully with no credentials

In MOCK_MODE=false (Sprint 6+):
  - Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env
  - Uses python-telegram-bot >= 21.x (async API)
  - Uses asyncio.run() wrapper for synchronous call sites

Alert types (per SCREENERS_MODULE_SPEC.md §S9, SPRINT_ROADMAP_v2.md §R7):
  - Morning brief (08:30 IST)
  - Screener result alerts (high-conviction setups)
  - Kill switch activation
  - Three-override demotion (paper mode 14 days)
  - Monthly circuit-breaker trigger
  - Strategy performance alert

Design rules:
  - All message content sanitised (no PII in logs)
  - Rate-limited: max 20 messages per minute (Telegram API limit)
  - Messages truncated at 4096 chars (Telegram limit)
  - Failed sends logged but never raise to caller (non-blocking)
  - MOCK_MODE check at module level

Documented in: SPRINT_ROADMAP_v2.md §R7.2, ARCHITECTURE_v5.md §29.4
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import time
from collections import deque
from typing import Any, Optional

from sentinel.core.types import utc_now

logger = logging.getLogger(__name__)

MOCK_MODE = os.getenv("MOCK_MODE", "true").lower() == "true"

TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

# Telegram limits
MAX_MESSAGE_LENGTH = 4096
RATE_LIMIT_PER_MINUTE = 20
RATE_WINDOW_SECONDS = 60


class TelegramBot:
    """
    Telegram alert delivery for Project Sentinel.

    Usage (mock mode — default):
        bot = TelegramBot()
        bot.send_message("Morning Brief ready")

    Usage (live mode — Sprint 6+):
        # Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to .env
        # Set MOCK_MODE=false
        bot = TelegramBot()
        bot.send_morning_brief(brief_text)
    """

    def __init__(
        self,
        token: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> None:
        self._token = token or TELEGRAM_BOT_TOKEN
        self._chat_id = chat_id or TELEGRAM_CHAT_ID
        self._mock = MOCK_MODE

        # Rate limiting: track timestamps of recent sends
        self._send_timestamps: deque[float] = deque()

        # Message counter for diagnostics
        self._sent_count = 0
        self._failed_count = 0

        if self._mock:
            logger.info(
                "[TelegramBot] Initialised in MOCK MODE. "
                "Messages will be logged, not sent to Telegram."
            )
        else:
            if not self._token or not self._chat_id:
                logger.warning(
                    "[TelegramBot] MOCK_MODE=false but TELEGRAM_BOT_TOKEN or "
                    "TELEGRAM_CHAT_ID not set. All sends will fail."
                )
            else:
                logger.info(
                    f"[TelegramBot] Initialised in LIVE MODE. "
                    f"Chat ID: {self._chat_id[:6]}..."
                )

    # ── Core send ─────────────────────────────────────────────────────────────

    def send_message(self, text: str, parse_mode: str = "Markdown") -> bool:
        """
        Send a message to the operator's Telegram chat.

        Returns True on success (or mock), False on failure.
        Never raises — failures are logged.
        """
        if not text:
            return False

        # Truncate to Telegram limit
        if len(text) > MAX_MESSAGE_LENGTH:
            text = text[: MAX_MESSAGE_LENGTH - 20] + "\n…[truncated]"

        # Rate limit check
        if not self._check_rate_limit():
            logger.warning(
                f"[TelegramBot] Rate limit reached ({RATE_LIMIT_PER_MINUTE}/min). "
                f"Message dropped."
            )
            return False

        if self._mock:
            # Log first 200 chars to avoid log spam
            preview = text[:200].replace("\n", " ")
            logger.info(f"[MOCK TELEGRAM] → {preview}")
            self._sent_count += 1
            return True

        # Live mode: use python-telegram-bot async API
        return self._send_live(text, parse_mode)

    def _send_live(self, text: str, parse_mode: str) -> bool:
        """Send via real Telegram API using asyncio.run()."""
        if importlib.util.find_spec("telegram") is None:
            logger.error(
                "[TelegramBot] python-telegram-bot not installed. "
                "Run: pip install python-telegram-bot"
            )
            self._failed_count += 1
            return False

        try:
            result = asyncio.run(self._async_send(text, parse_mode))
            if result:
                self._sent_count += 1
            else:
                self._failed_count += 1
            return result
        except Exception as e:
            logger.error(f"[TelegramBot] Send failed: {e}")
            self._failed_count += 1
            return False

    async def _async_send(self, text: str, parse_mode: str) -> bool:
        """Async send coroutine."""
        try:
            import telegram  # type: ignore[import]
            bot = telegram.Bot(token=self._token)
            await bot.send_message(
                chat_id=self._chat_id,
                text=text,
                parse_mode=parse_mode,
            )
            return True
        except Exception as e:
            logger.error(f"[TelegramBot] Async send error: {e}")
            return False

    def _check_rate_limit(self) -> bool:
        """
        Returns True if we are within the rate limit, False if over.
        Prunes old timestamps from the deque.
        """
        now = time.monotonic()
        # Remove timestamps older than the rate window
        while self._send_timestamps and now - self._send_timestamps[0] > RATE_WINDOW_SECONDS:
            self._send_timestamps.popleft()

        if len(self._send_timestamps) >= RATE_LIMIT_PER_MINUTE:
            return False

        self._send_timestamps.append(now)
        return True

    # ── Typed alert methods ──────────────────────────────────────────────────

    def send_morning_brief(self, brief_text: str) -> bool:
        """
        Send the morning brief to the operator at 08:30 IST.
        Called by scheduler.py.
        """
        now_ist = utc_now()
        header = (
            f"🌅 *Project Sentinel — Morning Brief*\n"
            f"_{now_ist.strftime('%d %b %Y, %H:%M UTC')}_\n\n"
        )
        return self.send_message(header + brief_text)

    def send_screener_alert(
        self,
        screener_id: str,
        symbol: str,
        conviction_score: float,
        direction: str,
        entry_zone: str,
        stop_loss: str,
        target_1: str,
        rr_ratio: float,
    ) -> bool:
        """
        Alert for a high-conviction screener setup.
        Called by scheduler when screener returns results.
        """
        emoji = "📈" if direction.lower() == "long" else "📉"
        text = (
            f"{emoji} *{screener_id.upper()} Alert — {symbol}*\n"
            f"Direction: {direction.upper()}\n"
            f"Conviction: {conviction_score:.0f}/100\n"
            f"Entry zone: {entry_zone}\n"
            f"Stop loss: {stop_loss}\n"
            f"Target 1: {target_1}\n"
            f"R:R 1:{rr_ratio:.1f}\n"
            f"_{utc_now().strftime('%H:%M UTC')}_"
        )
        return self.send_message(text)

    def send_kill_switch_alert(self, reason: str) -> bool:
        """
        Emergency alert when kill switch is activated.
        Highest priority — bypasses rate limit check.
        """
        text = (
            f"🚨 *KILL SWITCH ACTIVATED*\n"
            f"All new orders blocked.\n"
            f"Reason: {reason}\n"
            f"Time: {utc_now().strftime('%d %b %Y %H:%M UTC')}\n\n"
            f"Open dashboard to review positions.\n"
            f"Kill switch must be manually reset."
        )
        # Bypass rate limit for kill switch
        self._send_timestamps.clear()
        return self.send_message(text)

    def send_demotion_alert(self, override_count: int, paper_days: int = 14) -> bool:
        """
        Alert when Three-Override Rule demotes system to paper mode.
        Documented in: ARCHITECTURE_v5.md §23.9
        """
        text = (
            f"⚠️ *THREE-OVERRIDE RULE TRIGGERED*\n"
            f"{override_count} guardrail overrides in the last 30 days.\n"
            f"System demoted to PAPER MODE for {paper_days} days.\n\n"
            f"No live orders will be placed during this period.\n"
            f"Review your override log and reflect on the pattern.\n"
            f"A fresh §7.6 sign-off is required to resume live trading."
        )
        return self.send_message(text)

    def send_monthly_circuit_breaker_alert(
        self, monthly_loss_pct: float, limit_pct: float
    ) -> bool:
        """Alert when monthly loss circuit breaker fires."""
        text = (
            f"🔴 *MONTHLY CIRCUIT BREAKER TRIGGERED*\n"
            f"Monthly loss: {monthly_loss_pct:.1f}% (limit: {limit_pct:.1f}%)\n"
            f"No new positions until next month or operator review.\n"
            f"Open dashboard → Risk Center for details."
        )
        return self.send_message(text)

    def send_strategy_performance_alert(
        self,
        strategy_name: str,
        trailing_sharpe: float,
        threshold: float = 0.5,
        period_days: int = 90,
    ) -> bool:
        """
        Alert when strategy trailing Sharpe falls below threshold.
        Per SPRINT_ROADMAP_v2.md §R12.5 (trailing 12-month Sharpe).
        """
        emoji = "✅" if trailing_sharpe >= threshold else "⚠️"
        text = (
            f"{emoji} *{strategy_name} Performance Alert*\n"
            f"Trailing {period_days}-day Sharpe: {trailing_sharpe:.2f}\n"
            f"Threshold: {threshold:.2f}\n"
            f"{'Above threshold — monitoring continues.' if trailing_sharpe >= threshold else 'Below threshold — review strategy kill criteria.'}"
        )
        return self.send_message(text)

    def send_gsm_asm_alert(self, symbol: str, list_type: str = "GSM") -> bool:
        """Alert when a held position's stock is added to surveillance."""
        text = (
            f"⚠️ *SURVEILLANCE LIST ALERT*\n"
            f"*{symbol}* has been added to *{list_type}* list.\n"
            f"Per architecture rules: no new positions in this stock.\n"
            f"Review existing position immediately."
        )
        return self.send_message(text)

    def send_custom(self, title: str, body: str, emoji: str = "📌") -> bool:
        """Send a custom titled alert."""
        text = f"{emoji} *{title}*\n{body}"
        return self.send_message(text)

    # ── Diagnostics ──────────────────────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        """Return bot operational stats."""
        return {
            "mock_mode": self._mock,
            "sent_count": self._sent_count,
            "failed_count": self._failed_count,
            "rate_window_count": len(self._send_timestamps),
            "token_configured": bool(self._token),
            "chat_id_configured": bool(self._chat_id),
        }
