"""Async bridge between the service layer and GraphSync.

The service layer enqueues lightweight event dicts and returns immediately.
A single daemon thread drains the queue and calls the appropriate GraphSync
handler, keeping Neo4j latency entirely off the transactional request path.

Event schema
────────────
Every event is a plain dict with a mandatory "type" key:

  applicant_created   {"applicant_id": str}
  applicant_updated   {"applicant_id": str}
"""

import queue
import threading
from typing import Any, Dict, Optional

from mltek.common.log.logging_config import setup_structured_logging
from mltek.common.infra.context import CorrelationContext, get_env

logger = setup_structured_logging(__name__)


class GraphSyncWorker:
    """Daemon thread that drains the sync event queue."""

    def __init__(self, maxsize: int = 10_000) -> None:
        self._queue: queue.Queue[Dict[str, Any]] = queue.Queue(maxsize=maxsize)
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="graph-sync-worker",
        )
        self._thread.start()
        logger.info("GraphSyncWorker started: thread=%s queue_maxsize=%d", self._thread.name, maxsize)

    # ── Public API ────────────────────────────────────────────────────────────

    def enqueue(self, event: Dict[str, Any]) -> None:
        """Put an event on the queue. Non-blocking; drops event if queue is full.
        No-op for non-prod — shadow data must not sync to the graph."""
        if get_env() != 'prod':
            logger.debug("GraphSync skipped env=%s event=%s", get_env(), event.get('type'))
            return
        event.setdefault("correlation_id", CorrelationContext.get() or "")
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            logger.warning("GraphSync queue full — dropping event: %s correlation_id=%s", event.get('type'), event.get('correlation_id'))

    def drain(self) -> None:
        """Block until all queued events have been processed (useful in tests)."""
        self._queue.join()

    # ── Worker loop ───────────────────────────────────────────────────────────

    def _run(self) -> None:
        from core_data.db.graph.graph_sync import get_graph_sync  # lazy import avoids circular deps

        while True:
            try:
                event = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            correlation_id = event.get("correlation_id", "")
            try:
                self._dispatch(get_graph_sync(), event)
            except Exception:
                logger.error("GraphSync worker failed on event %s correlation_id=%s", event.get('type'), correlation_id, exc_info=True)
            finally:
                self._queue.task_done()

    def _dispatch(self, sync, event: Dict[str, Any]) -> None:
        etype = event.get("type")
        correlation_id = event.get("correlation_id", "")
        logger.info("GraphSync dispatch type=%s correlation_id=%s", etype, correlation_id)
        match etype:
            case "applicant_created" | "applicant_updated":
                sync.sync_applicant(event["applicant_id"])
            case _:
                logger.warning("GraphSyncWorker: unknown event type '%s'", etype)


# ── Singleton ─────────────────────────────────────────────────────────────────

_worker: Optional[GraphSyncWorker] = None
_worker_lock = threading.Lock()


def get_sync_worker() -> GraphSyncWorker:
    global _worker
    if _worker is None:
        with _worker_lock:
            if _worker is None:
                _worker = GraphSyncWorker()
    return _worker
