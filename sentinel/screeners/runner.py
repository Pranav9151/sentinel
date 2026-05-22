"""
sentinel/screeners/runner.py
==============================
ScreenerRunner — orchestrates all 7 screeners.

Usage:
    runner = ScreenerRunner()
    results = runner.run_all()
    s1_results = results["s1_momentum"]

The dashboard calls run_all() with Streamlit caching.
The scheduler calls individual screeners at their scheduled times.

Documented in: SCREENERS_MODULE_SPEC.md §S8
"""

from __future__ import annotations

import logging
from typing import Any

from sentinel.screeners.s1_momentum import MomentumBreakoutScreener
from sentinel.screeners.s2_s7 import (
    ValueReversalScreener,
    SectorMomentumScreener,
    PennySmallCapScreener,
    SmartInstitutionalScreener,
    MFConvictionScreener,
    ForexOpportunityScreener,
)

logger = logging.getLogger(__name__)


class ScreenerRunner:
    """
    Runs all 7 screeners and returns a combined results dict.

    The 50:1 funnel (SCREENERS_MODULE_SPEC.md §S8.2):
      Universe: ~35 mock stocks (500 in live)
      → Raw candidates across all screeners: ~20-40
      → After score filter: ~15-25
      → Final results (capped per screener): ~15-25
      → Operator reviews Cards: 5-15
      → Operator executes: 1-3 trades per week
    """

    def __init__(self) -> None:
        self.screeners = {
            "s1_momentum":      MomentumBreakoutScreener(),
            "s2_value":         ValueReversalScreener(),
            "s3_sector":        SectorMomentumScreener(),
            "s4_penny":         PennySmallCapScreener(),
            "s5_institutional": SmartInstitutionalScreener(),
            "s6_mf":            MFConvictionScreener(),
            "s7_forex":         ForexOpportunityScreener(),
        }

    def run_all(self) -> dict[str, Any]:
        """Run all screeners and return combined results."""
        results = {}
        total_candidates = 0

        for name, screener in self.screeners.items():
            try:
                result = screener.run()
                results[name] = result
                count = len(result.get("candidates", []))
                total_candidates += count
                logger.info(f"[{name}] {count} candidates")
            except Exception as e:
                logger.error(f"[{name}] Failed: {e}", exc_info=True)
                results[name] = {
                    "screener": name,
                    "error": str(e),
                    "candidates": [],
                    "meta": {},
                }

        results["_summary"] = {
            "total_candidates": total_candidates,
            "screeners_run": len(self.screeners),
            "screeners_with_results": sum(
                1 for r in results.values()
                if isinstance(r, dict) and r.get("candidates")
            ),
        }
        logger.info(
            f"All screeners complete: {total_candidates} total candidates"
        )
        return results

    def run_one(self, screener_name: str) -> dict[str, Any]:
        """Run a single screener by name."""
        screener = self.screeners.get(screener_name)
        if not screener:
            return {
                "error": f"Unknown screener: {screener_name}",
                "candidates": [],
            }
        return screener.run()
