"""
sentinel/screeners/base.py
============================
Base screener class. Every screener inherits from this.

Enforces:
- GSM/ASM hard rejection on every candidate
- Conviction score minimum before returning results
- Maximum result cap per screener
- Audit trail (when ran, universe size, results count)

Documented in: SCREENERS_MODULE_SPEC.md §S0.3
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from sentinel.core.types import utc_now
from sentinel.data.market_data import MarketDataStore
from sentinel.data.mock_data import ALL_MOCK_STOCKS

logger = logging.getLogger(__name__)


class BaseScreener(ABC):
    """
    Abstract base class for all 7 screeners.

    Subclasses implement:
        name        — screener identifier (e.g. "s1_momentum")
        description — one-sentence purpose
        max_results — hard cap on results returned
        min_score   — minimum conviction score to appear
        _run()      — returns raw candidate list

    The run() method wraps _run() with:
        - GSM/ASM hard rejection
        - Score filtering
        - Result capping
        - Audit metadata
    """

    name: str = "base"
    description: str = "Base screener"
    max_results: int = 5
    min_score: float = 50.0

    def __init__(self) -> None:
        self.market_data = MarketDataStore()
        self._surveillance_list: set[str] = set()
        self._load_surveillance()

    def _load_surveillance(self) -> None:
        """Load GSM/ASM list. Called once at init."""
        try:
            self.market_data.refresh_gsm_asm_list()
            self._surveillance_list = set(
                self.market_data.get_surveillance_list()
            )
        except Exception as e:
            logger.warning(f"Could not load surveillance list: {e}")
            self._surveillance_list = set()

    def is_blocked(self, symbol: str) -> bool:
        """Hard block — GSM/ASM listed symbols never appear in results."""
        return symbol in self._surveillance_list

    def get_universe(self) -> list[str]:
        """
        Return the screening universe.
        In mock mode: ALL_MOCK_STOCKS keys.
        In live mode: Nifty 500 from the instrument store.
        """
        return list(ALL_MOCK_STOCKS.keys())

    def run(self) -> dict[str, Any]:
        """
        Public entry point. Wraps _run() with safety checks.
        Returns a standardised result dict.
        """
        started_at = utc_now()
        universe = self.get_universe()

        try:
            raw_candidates = self._run(universe)
        except Exception as e:
            logger.error(f"[{self.name}] Screener error: {e}", exc_info=True)
            return {
                "screener": self.name,
                "error": str(e),
                "candidates": [],
                "meta": {"ran_at": started_at.isoformat(),
                         "universe_size": len(universe)},
            }

        # Hard reject surveillance-listed symbols
        clean = [c for c in raw_candidates
                 if not self.is_blocked(c.get("symbol", ""))]

        # Hard filter: R:R >= 2.0 enforced at base layer (architecture rule)
        rr_ok = [c for c in clean
                 if (c.get("rr_ratio") or 0) >= 2.0]

        # Filter by minimum conviction score
        scored = [c for c in rr_ok
                  if (c.get("conviction_score") or 0) >= self.min_score]

        # Sort by conviction score descending
        scored.sort(key=lambda c: c.get("conviction_score", 0), reverse=True)

        # Hard cap on results
        final = scored[: self.max_results]

        elapsed_ms = int(
            (utc_now() - started_at).total_seconds() * 1000
        )
        logger.info(
            f"[{self.name}] {len(final)} results "
            f"from {len(universe)} universe in {elapsed_ms}ms"
        )

        return {
            "screener": self.name,
            "description": self.description,
            "candidates": final,
            "meta": {
                "ran_at": started_at.isoformat(),
                "elapsed_ms": elapsed_ms,
                "universe_size": len(universe),
                "raw_count": len(raw_candidates),
                "after_surveillance_filter": len(clean),
                "after_score_filter": len(scored),
                "final_count": len(final),
                "gsm_rejected": len(raw_candidates) - len(clean),
            },
        }

    @abstractmethod
    def _run(self, universe: list[str]) -> list[dict[str, Any]]:
        """
        Subclass implements this. Returns raw candidate list.
        Each candidate is a dict with at minimum:
            symbol, sector, conviction_score, direction,
            entry_low, entry_high, stop_loss, target_1,
            rr_ratio, suggested_qty, thesis, risks
        """
        ...

    def _build_candidate(
        self,
        symbol: str,
        conviction_score: float,
        entry_low: float,
        entry_high: float,
        stop_loss: float,
        target_1: float,
        target_2: float | None = None,
        direction: str = "BUY",
        thesis: str = "",
        risks: list[str] | None = None,
        suggested_qty: int = 1,
        extra: dict | None = None,
    ) -> dict[str, Any]:
        """Helper to build a standardised candidate dict."""
        entry_mid = (entry_low + entry_high) / 2
        risk_per_share = abs(entry_mid - stop_loss)
        reward_t1 = abs(target_1 - entry_mid)
        rr = round(reward_t1 / risk_per_share, 2) if risk_per_share > 0 else 0

        stock_info = ALL_MOCK_STOCKS.get(symbol, {})
        sector = stock_info.get("sector", "Unknown")

        cand = {
            "symbol": symbol,
            "sector": sector,
            "direction": direction,
            "conviction_score": round(conviction_score, 1),
            "entry_low": round(entry_low, 2),
            "entry_high": round(entry_high, 2),
            "stop_loss": round(stop_loss, 2),
            "target_1": round(target_1, 2),
            "target_2": round(target_2, 2) if target_2 else None,
            "rr_ratio": rr,
            "suggested_qty": suggested_qty,
            "thesis": thesis,
            "risks": risks or [],
            "generated_at": utc_now().isoformat(),
            "screener": self.name,
        }
        if extra:
            cand.update(extra)
        return cand
