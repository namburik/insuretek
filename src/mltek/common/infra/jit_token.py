"""JIT privilege token store — HMAC-SHA256, single-use, 30-second TTL."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import secrets
import time

from mltek.common.log import setup_structured_logging

logger = setup_structured_logging(__name__)

_TTL_SECONDS = 30

# Load key once at import; fall back to a process-local random so tests
# work without env configuration.
_raw = os.getenv("JIT_HMAC_SECRET", "")
_HMAC_KEY: bytes = bytes.fromhex(_raw) if len(_raw) >= 32 else secrets.token_bytes(32)


def _derive_hmac(tool_name: str, session_id: str, nonce: str, timestamp: int) -> str:
    payload = f"{tool_name}:{session_id}:{nonce}:{timestamp}".encode()
    return hmac.new(_HMAC_KEY, payload, hashlib.sha256).hexdigest()


class JitTokenStore:
    """Singleton async-safe nonce store for single-use JIT tokens.

    Token wire format: "<nonce>.<timestamp>.<hmac>"
    Bound to (tool_name, session_id) — tokens cannot be replayed across tools or sessions.
    """

    def __init__(self) -> None:
        self._nonces: dict[str, float] = {}  # nonce → expiry epoch
        self._lock: asyncio.Lock = asyncio.Lock()

    def issue(self, tool_name: str, session_id: str) -> str:
        """Issue a single-use token (synchronous — no shared-state write)."""
        nonce = secrets.token_hex(8)
        ts = int(time.time())
        sig = _derive_hmac(tool_name, session_id, nonce, ts)
        self._nonces[nonce] = ts + _TTL_SECONDS
        logger.info("JIT token issued tool=%s session=%s nonce=%s", tool_name, session_id[:8], nonce)
        return f"{nonce}.{ts}.{sig}"

    async def consume(self, token: str, tool_name: str, session_id: str) -> bool:
        """Verify and invalidate a token. Returns True only on first valid use."""
        try:
            nonce, ts_str, sig = token.split(".", 2)
            ts = int(ts_str)
        except (ValueError, AttributeError):
            logger.warning("JIT token malformed tool=%s", tool_name)
            return False

        expected = _derive_hmac(tool_name, session_id, nonce, ts)
        if not hmac.compare_digest(expected, sig):
            logger.warning("JIT token HMAC mismatch tool=%s session=%s", tool_name, session_id[:8])
            return False

        if time.time() - ts > _TTL_SECONDS:
            logger.warning("JIT token expired tool=%s session=%s age=%ds", tool_name, session_id[:8], int(time.time() - ts))
            return False

        async with self._lock:
            self._prune()
            if nonce not in self._nonces:
                logger.warning("JIT token already consumed or unknown nonce tool=%s", tool_name)
                return False
            del self._nonces[nonce]

        logger.info("JIT token consumed tool=%s session=%s", tool_name, session_id[:8])
        return True

    def _prune(self) -> None:
        now = time.time()
        expired = [n for n, exp in self._nonces.items() if exp <= now]
        for n in expired:
            del self._nonces[n]


# Module-level singleton shared across all middleware instances.
_token_store = JitTokenStore()


def get_token_store() -> JitTokenStore:
    return _token_store
