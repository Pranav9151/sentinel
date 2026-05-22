"""
sentinel/core/premortem.py
============================
Pre-Mortem Journal — 5 questions before every trade.

Research basis: Gary Klein (2007) pre-mortem technique.
Forces the operator to articulate the thesis BEFORE entering,
which dramatically reduces post-hoc rationalization.

The journal builds a longitudinal dataset of operator decisions.
After 6 months, it reveals personal bias patterns that no
generic guardrail could detect.

Documented in: OPUS_INDEPENDENT_PROPOSALS.md §F5,
               ARCHITECTURE_v5.md §23
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from sentinel.core.types import utc_now

logger = logging.getLogger(__name__)

JOURNAL_PATH = Path("premortem_journal.json")

QUESTIONS = [
    {
        "id": "q1_thesis",
        "question": "What is the specific reason I am entering this trade?",
        "hint": "Name the exact signal: screener, indicator, pattern, catalyst.",
        "required": True,
    },
    {
        "id": "q2_wrong",
        "question": "How could I be wrong about this trade?",
        "hint": "Name 2-3 specific scenarios where this fails. Be honest.",
        "required": True,
    },
    {
        "id": "q3_exit",
        "question": "What will I see that tells me I am wrong and should exit?",
        "hint": "Price level, time limit, or fundamental change that invalidates thesis.",
        "required": True,
    },
    {
        "id": "q4_bias",
        "question": "Am I entering because of a tip, fear of missing out, or the system?",
        "hint": "Honest answer only. If tip or FOMO → stop here.",
        "required": True,
    },
    {
        "id": "q5_size",
        "question": "Why is this position size appropriate for this conviction level?",
        "hint": "Reference your conviction score and the 1-2% risk rule.",
        "required": False,
    },
]

# Answers that suggest the operator is not thinking carefully
TEMPLATED_RESPONSES = {
    "looks good", "seems fine", "will work out",
    "going up", "strong stock", "good company",
    "na", "n/a", "ok", "yes", "no", "maybe",
}


@dataclass
class PreMortemEntry:
    """A single pre-mortem journal entry."""
    entry_id:    str
    symbol:      str
    screener:    str
    timestamp:   str
    answers:     dict[str, str]
    conviction_score: float
    entry_price: float
    stop_loss:   float
    target_1:    float

    # Filled in later (post-trade)
    outcome:     Optional[str]  = None  # "win"/"loss"/"scratch"
    exit_price:  Optional[float] = None
    exit_date:   Optional[str]  = None
    pnl_pct:     Optional[float] = None
    lesson:      Optional[str]  = None

    # Quality flag — set if answers look templated
    low_quality: bool = False
    low_quality_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id":        self.entry_id,
            "symbol":          self.symbol,
            "screener":        self.screener,
            "timestamp":       self.timestamp,
            "answers":         self.answers,
            "conviction_score":self.conviction_score,
            "entry_price":     self.entry_price,
            "stop_loss":       self.stop_loss,
            "target_1":        self.target_1,
            "outcome":         self.outcome,
            "exit_price":      self.exit_price,
            "exit_date":       self.exit_date,
            "pnl_pct":         self.pnl_pct,
            "lesson":          self.lesson,
            "low_quality":     self.low_quality,
            "low_quality_reason": self.low_quality_reason,
        }


class PreMortemJournal:
    """
    Manages pre-mortem journal entries.

    Usage:
        journal = PreMortemJournal()

        # Before trade
        entry_id = journal.create_entry(
            symbol="RELIANCE",
            screener="s1_momentum",
            answers={
                "q1_thesis": "Breaking 52W high on 2.3x volume...",
                "q2_wrong": "False breakout if market selloff...",
                "q3_exit": "Exit if price closes below ₹2870...",
                "q4_bias": "System signal from S1 screener...",
                "q5_size": "1% risk = 37 shares at this stop distance...",
            },
            conviction_score=72.0,
            entry_price=2950.0,
            stop_loss=2870.0,
            target_1=3100.0,
        )

        # After trade closes
        journal.update_outcome(entry_id, "win", exit_price=3095.0)
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or JOURNAL_PATH
        self._entries: list[dict] = []
        self._counter: int = 0
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                with open(self._path) as f:
                    self._entries = json.load(f)
            except Exception:
                self._entries = []

    def _save(self) -> None:
        try:
            with open(self._path, "w") as f:
                json.dump(self._entries, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save journal: {e}")

    def _generate_id(self) -> str:
        """Generate a guaranteed-unique entry ID using counter + microseconds."""
        now = utc_now()
        self._counter += 1
        return f"pm_{now.strftime('%Y%m%d_%H%M%S')}_{now.microsecond:06d}_{self._counter:04d}"

    def _check_quality(self, answers: dict[str, str]) -> tuple[bool, str]:
        """
        Flag entries where answers look templated or too short.
        Short/generic answers defeat the purpose of the pre-mortem.
        """
        for qid, answer in answers.items():
            answer_lower = answer.strip().lower()

            # Too short
            if len(answer.strip()) < 20:
                return True, f"Answer to {qid} is too short ({len(answer.strip())} chars). Min 20."

            # Templated response
            if answer_lower in TEMPLATED_RESPONSES:
                return True, f"Answer to {qid} appears templated: '{answer.strip()[:30]}'"

        return False, ""

    def create_entry(
        self,
        symbol: str,
        screener: str,
        answers: dict[str, str],
        conviction_score: float,
        entry_price: float,
        stop_loss: float,
        target_1: float,
    ) -> str:
        """
        Create a new pre-mortem entry. Returns entry_id.
        Checks answer quality and flags low-quality entries.
        """
        # Validate required questions answered
        for q in QUESTIONS:
            if q["required"] and q["id"] not in answers:
                raise ValueError(
                    f"Required question '{q['id']}' not answered. "
                    "All required questions must be answered before entry."
                )

        entry_id = self._generate_id()
        low_quality, reason = self._check_quality(answers)

        entry = PreMortemEntry(
            entry_id=entry_id,
            symbol=symbol,
            screener=screener,
            timestamp=utc_now().isoformat(),
            answers=answers,
            conviction_score=conviction_score,
            entry_price=entry_price,
            stop_loss=stop_loss,
            target_1=target_1,
            low_quality=low_quality,
            low_quality_reason=reason,
        )

        self._entries.append(entry.to_dict())
        self._save()

        if low_quality:
            logger.warning(
                f"Low-quality pre-mortem for {symbol}: {reason}. "
                "Short or generic answers defeat the purpose of the journal."
            )
        else:
            logger.info(f"Pre-mortem entry created: {entry_id} for {symbol}")

        return entry_id

    def update_outcome(
        self,
        entry_id: str,
        outcome: str,
        exit_price: float,
        lesson: str = "",
    ) -> bool:
        """Update a journal entry with the trade outcome."""
        for entry in self._entries:
            if entry["entry_id"] == entry_id:
                ep = entry.get("entry_price", 0)
                pnl = (exit_price - ep) / ep * 100 if ep > 0 else 0

                entry.update({
                    "outcome":    outcome,
                    "exit_price": exit_price,
                    "exit_date":  utc_now().isoformat(),
                    "pnl_pct":    round(pnl, 2),
                    "lesson":     lesson,
                })
                self._save()
                logger.info(f"Outcome updated for {entry_id}: {outcome} {pnl:+.1f}%")
                return True
        return False

    def get_all(self) -> list[dict]:
        return list(self._entries)

    def get_open(self) -> list[dict]:
        return [e for e in self._entries if e.get("outcome") is None]

    def get_closed(self) -> list[dict]:
        return [e for e in self._entries if e.get("outcome") is not None]

    def get_analytics(self) -> dict[str, Any]:
        """
        Longitudinal analytics — reveals personal bias patterns.
        Meaningful after 20+ entries (approx 6-8 weeks of trading).
        """
        closed = self.get_closed()
        if not closed:
            return {"entries": 0, "message": "No closed trades yet."}

        total   = len(closed)
        wins    = [e for e in closed if e.get("outcome") == "win"]
        losses  = [e for e in closed if e.get("outcome") == "loss"]
        win_rate = len(wins) / total * 100 if total > 0 else 0

        # Average P&L
        pnls = [e.get("pnl_pct", 0) or 0 for e in closed]
        avg_pnl  = sum(pnls) / len(pnls) if pnls else 0
        avg_win  = sum(e.get("pnl_pct", 0) or 0 for e in wins) / len(wins) if wins else 0
        avg_loss = sum(e.get("pnl_pct", 0) or 0 for e in losses) / len(losses) if losses else 0

        # Low quality entry outcomes
        low_q = [e for e in closed if e.get("low_quality")]
        low_q_wins = [e for e in low_q if e.get("outcome") == "win"]
        low_q_win_rate = len(low_q_wins) / len(low_q) * 100 if low_q else None

        # Screener performance
        screener_stats: dict[str, dict] = {}
        for e in closed:
            sc = e.get("screener", "unknown")
            if sc not in screener_stats:
                screener_stats[sc] = {"total": 0, "wins": 0}
            screener_stats[sc]["total"] += 1
            if e.get("outcome") == "win":
                screener_stats[sc]["wins"] += 1

        for sc, stats in screener_stats.items():
            stats["win_rate"] = round(
                stats["wins"] / stats["total"] * 100, 1
            ) if stats["total"] > 0 else 0

        return {
            "entries":      total,
            "win_rate_pct": round(win_rate, 1),
            "avg_pnl_pct":  round(avg_pnl, 2),
            "avg_win_pct":  round(avg_win, 2),
            "avg_loss_pct": round(avg_loss, 2),
            "expectancy":   round(
                (win_rate/100 * avg_win) + ((1 - win_rate/100) * avg_loss), 2
            ),
            "low_quality_entries": len(low_q),
            "low_quality_win_rate": round(low_q_win_rate, 1) if low_q_win_rate else None,
            "screener_performance": screener_stats,
            "insight": self._generate_insight(win_rate, avg_win, avg_loss, low_q),
        }

    def _generate_insight(
        self,
        win_rate: float,
        avg_win: float,
        avg_loss: float,
        low_quality: list,
    ) -> str:
        """Generate a key insight from the journal data."""
        insights = []

        # Disposition effect check
        if avg_win > 0 and avg_loss < 0:
            ratio = abs(avg_win / avg_loss)
            if ratio < 1.0:
                insights.append(
                    f"Disposition effect detected: average win ({avg_win:.1f}%) "
                    f"is smaller than average loss ({avg_loss:.1f}%). "
                    "You are cutting winners too early and holding losers too long."
                )
            elif ratio > 2.0:
                insights.append(
                    f"Good asymmetry: wins ({avg_win:.1f}%) "
                    f"are {ratio:.1f}x losses ({avg_loss:.1f}%)."
                )

        # Low quality impact
        if len(low_quality) >= 5:
            insights.append(
                f"{len(low_quality)} trades had low-quality pre-mortems. "
                "Trades with thorough pre-mortems tend to perform better — "
                "the thinking process itself improves decision quality."
            )

        # Win rate context
        if win_rate < 40:
            insights.append(
                f"Win rate {win_rate:.0f}% is below 40%. "
                "Check if you are exiting winners too early."
            )
        elif win_rate > 70:
            insights.append(
                f"Win rate {win_rate:.0f}% is high. "
                "Verify you are not cutting losses too quickly (small losses count as wins)."
            )

        return " | ".join(insights) if insights else "Insufficient data for insight."
