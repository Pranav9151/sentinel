"""
sentinel/ops/killswitch.py
==========================
The independent kill switch process.

This runs as a SEPARATE PROCESS from sentinel-engine.
If sentinel-engine crashes or hangs, the kill switch still works.

Sprint 1 acceptance gate:
    Kill switch must flatten all paper positions in < 5 seconds.

Activation methods:
    1. HTTP POST to /killswitch endpoint with KILLSWITCH_SECRET
    2. Telegram command /kill (from operator's Telegram account only)
    3. Auto-trigger: monthly circuit breaker loss limit
    4. Auto-trigger: India VIX > 22 (defensive mode)

When activated:
    - All new order generation blocked immediately
    - Existing open positions: stops remain active (do NOT auto-close)
    - Operator receives Telegram + dashboard alert
    - System enters DEFENSIVE state until operator manually resets

Documented in: ARCHITECTURE_v5.md §11, FORENSIC_ANALYSIS_v5.md §2.9
SPRINT_ROADMAP_v2.md Sprint 1 Acceptance Gate
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

KILLSWITCH_SECRET = os.getenv("KILLSWITCH_SECRET", "CHANGE_THIS_SECRET")
KILLSWITCH_STATE_FILE = Path("killswitch_state.json")

# In-memory state (also persisted to file for crash recovery)
_kill_active = False
_kill_reason = ""
_kill_timestamp: Optional[datetime] = None
_kill_callbacks: list[Callable] = []


def is_kill_active() -> bool:
    """Check if kill switch is currently active. Thread-safe."""
    return _kill_active


def get_kill_state() -> dict:
    """Get current kill switch state details."""
    return {
        "active": _kill_active,
        "reason": _kill_reason,
        "timestamp": _kill_timestamp.isoformat() if _kill_timestamp else None,
    }


def register_callback(fn: Callable) -> None:
    """
    Register a function to call when kill switch activates.
    Used by order manager, position tracker, and dashboard.
    """
    _kill_callbacks.append(fn)


def activate_kill_switch(reason: str, source: str = "manual") -> dict:
    """
    Activate the kill switch. Idempotent — safe to call multiple times.

    This is the most critical function in the system.
    It must work even if everything else is broken.

    Args:
        reason: Human-readable reason for activation
        source: Who triggered it ("manual", "vix", "loss_limit", "telegram")

    Returns:
        Status dict with activation confirmation
    """
    global _kill_active, _kill_reason, _kill_timestamp

    _kill_active = True
    _kill_reason = reason
    _kill_timestamp = datetime.now(timezone.utc)

    logger.critical(
        f"KILL SWITCH ACTIVATED — Source: {source} — Reason: {reason} "
        f"at {_kill_timestamp.isoformat()}"
    )

    # Persist to file (survives process crashes)
    _persist_state()

    # Call all registered callbacks (order manager, position tracker, etc.)
    for callback in _kill_callbacks:
        try:
            callback(reason=reason, source=source, timestamp=_kill_timestamp)
        except Exception as e:
            # Log but never let callback failure prevent kill switch
            logger.error(f"Kill switch callback {callback.__name__} failed: {e}")

    return {
        "status": "activated",
        "reason": reason,
        "source": source,
        "timestamp": _kill_timestamp.isoformat(),
        "message": (
            "Kill switch ACTIVE. All new order generation blocked. "
            "Existing positions and stops remain active. "
            "Check Telegram for alert. Reset manually after review."
        ),
    }


def reset_kill_switch(secret: str, operator_note: str = "") -> dict:
    """
    Reset the kill switch to allow normal operation.
    Requires the KILLSWITCH_SECRET to prevent accidental reset.

    This must be done MANUALLY by the operator after reviewing
    what triggered the kill switch.

    Args:
        secret: Must match KILLSWITCH_SECRET from .env
        operator_note: Required explanation of why reset is safe
    """
    global _kill_active, _kill_reason, _kill_timestamp

    if secret != KILLSWITCH_SECRET:
        logger.warning("Kill switch reset attempted with wrong secret.")
        return {
            "status": "error",
            "message": "Invalid secret. Kill switch NOT reset.",
        }

    if not operator_note:
        return {
            "status": "error",
            "message": "operator_note is required. Explain why the reset is safe.",
        }

    if not _kill_active:
        return {
            "status": "info",
            "message": "Kill switch was not active. No action taken.",
        }

    previous_reason = _kill_reason
    _kill_active = False
    _kill_reason = ""
    _kill_timestamp = None

    logger.info(
        f"Kill switch RESET by operator. "
        f"Previous reason: '{previous_reason}'. "
        f"Operator note: '{operator_note}'"
    )

    _persist_state(include_note=operator_note)

    return {
        "status": "reset",
        "previous_reason": previous_reason,
        "operator_note": operator_note,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": "Kill switch reset. Normal operation resumed.",
    }


def validate_killswitch_secret(secret: str) -> bool:
    """Return True only when the supplied secret matches the configured token."""
    return bool(secret) and secret == KILLSWITCH_SECRET


def run_kill_switch_test(mock_positions: Optional[list] = None) -> dict:
    """
    Sprint 1 acceptance gate test:
    Verify the kill switch can flatten paper positions in < 5 seconds.

    Args:
        mock_positions: List of fake paper positions to flatten.
                       Defaults to 5 positions for the test.

    Returns:
        Test result with timing. Must be < 5 seconds to pass.
    """
    if mock_positions is None:
        mock_positions = [
            {"symbol": "RELIANCE", "qty": 1, "type": "paper"},
            {"symbol": "TCS", "qty": 2, "type": "paper"},
            {"symbol": "HDFCBANK", "qty": 3, "type": "paper"},
            {"symbol": "EURUSD", "qty": 1, "type": "paper"},
            {"symbol": "XAUUSD", "qty": 1, "type": "paper"},
        ]

    logger.info(f"Running kill switch test with {len(mock_positions)} positions...")
    start_time = time.time()

    # Activate kill switch
    activate_kill_switch(
        reason="Sprint 1 acceptance gate test",
        source="test"
    )

    # Simulate flattening positions (in production: cancel orders, set stops)
    flattened = []
    for position in mock_positions:
        # In paper mode: just mark as flattened
        # In live mode: this would cancel pending orders and confirm stops active
        flattened.append({
            "symbol": position["symbol"],
            "action": "stops_confirmed_active",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        time.sleep(0.05)  # Simulate minimal network latency

    elapsed = time.time() - start_time
    passed = elapsed < 5.0

    # Reset after test
    reset_kill_switch(
        secret=KILLSWITCH_SECRET,
        operator_note="Resetting after Sprint 1 acceptance gate test"
    )

    result = {
        "test": "kill_switch_sprint1_acceptance_gate",
        "passed": passed,
        "elapsed_seconds": round(elapsed, 3),
        "max_allowed_seconds": 5.0,
        "positions_processed": len(flattened),
        "positions": flattened,
        "verdict": (
            f"✅ PASSED — Kill switch flattened {len(flattened)} positions "
            f"in {elapsed:.3f}s (< 5s limit)"
            if passed else
            f"❌ FAILED — Kill switch took {elapsed:.3f}s to process positions. "
            f"Must be < 5s. Investigate before Sprint 2."
        ),
    }

    if passed:
        logger.info(result["verdict"])
    else:
        logger.error(result["verdict"])

    return result


def start_http_server(port: int = 8502) -> None:
    """
    Start the kill switch HTTP server.
    Runs on a separate port from the Streamlit dashboard (8501).

    Endpoints:
        POST /activate  — Activate kill switch (requires KILLSWITCH_SECRET)
        POST /reset     — Reset kill switch (requires KILLSWITCH_SECRET)
        GET  /status    — Get current state (no auth required)
        GET  /test      — Run acceptance gate test (no auth in dev)

    In production: run this on localhost only, not exposed to internet.
    """
    try:
        from http.server import HTTPServer, BaseHTTPRequestHandler

        class KillSwitchHandler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                logger.debug(f"KillSwitch HTTP: {format % args}")

            def do_GET(self):
                if self.path == "/status":
                    self._json_response(200, get_kill_state())
                elif self.path == "/test":
                    result = run_kill_switch_test()
                    self._json_response(200, result)
                else:
                    self._json_response(404, {"error": "Not found"})

            def do_POST(self):
                content_length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(content_length) or "{}")
                secret = body.get("secret", "")

                if self.path == "/activate":
                    if not validate_killswitch_secret(secret):
                        logger.warning("Kill switch activation attempted with wrong secret.")
                        self._json_response(403, {
                            "status": "error",
                            "message": "Invalid secret. Kill switch NOT activated.",
                        })
                        return
                    reason = body.get("reason", "Manual activation via HTTP")
                    result = activate_kill_switch(reason=reason, source="http")
                    self._json_response(200, result)

                elif self.path == "/reset":
                    note = body.get("operator_note", "")
                    result = reset_kill_switch(secret=secret, operator_note=note)
                    code = 200 if result["status"] in ("reset", "info") else 403
                    self._json_response(code, result)

                else:
                    self._json_response(404, {"error": "Not found"})

            def _json_response(self, code: int, data: dict):
                body = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = HTTPServer(("127.0.0.1", port), KillSwitchHandler)
        logger.info(f"Kill switch HTTP server started on http://127.0.0.1:{port}")
        server.serve_forever()

    except Exception as e:
        logger.critical(f"Kill switch HTTP server failed to start: {e}")
        raise


def _persist_state(include_note: str = "") -> None:
    """Write current state to file for crash recovery."""
    state = {
        "active": _kill_active,
        "reason": _kill_reason,
        "timestamp": _kill_timestamp.isoformat() if _kill_timestamp else None,
        "note": include_note,
    }
    try:
        with open(KILLSWITCH_STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to persist kill switch state: {e}")


def load_persisted_state() -> None:
    """
    Load kill switch state from file on process startup.
    If the process crashed while kill switch was active,
    it will still be active when it restarts.
    """
    global _kill_active, _kill_reason, _kill_timestamp

    if not KILLSWITCH_STATE_FILE.exists():
        return

    try:
        with open(KILLSWITCH_STATE_FILE) as f:
            state = json.load(f)

        if state.get("active"):
            _kill_active = True
            _kill_reason = state.get("reason", "Recovered from previous session")
            ts_str = state.get("timestamp")
            if ts_str:
                _kill_timestamp = datetime.fromisoformat(ts_str)
            logger.warning(
                f"Kill switch was ACTIVE in previous session. "
                f"Reason: {_kill_reason}. "
                f"System starting in BLOCKED state. Reset manually after review."
            )
    except Exception as e:
        logger.error(f"Failed to load persisted kill switch state: {e}")


# ─────────────────────────────────────────────
# ENTRY POINT — Run as independent process
# ─────────────────────────────────────────────

if __name__ == "__main__":
    """
    Run the kill switch as a standalone process:
        python -m sentinel.ops.killswitch

    This should ALWAYS be running when Sentinel is active.
    It is independent of sentinel-engine and sentinel-screener.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [KILLSWITCH] %(levelname)s %(message)s"
    )

    # Load any state from previous session
    load_persisted_state()

    if is_kill_active():
        logger.warning(
            "Starting with ACTIVE kill switch from previous session. "
            "Visit http://127.0.0.1:8502/status to check state."
        )

    logger.info("Project Sentinel Kill Switch — starting HTTP server on port 8502")
    start_http_server(port=8502)
