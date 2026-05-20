"""MES recovery endpoints — called by the n8n self-healing workflow.

Architecture (Phase 4a):

    n8n workflow                       FastAPI backend (this package)
    ────────────                       ──────────────────────────────
    Execute *Recovery   ──HTTP POST──▶  routes.py
                                            │
                                            ├─▶ idempotency.py
                                            │   (Redis dedup: skip the
                                            │   workflow's 3 retries
                                            │   for the same target)
                                            │
                                            ├─▶ circuit_breaker.py
                                            │   (Redis state machine:
                                            │   per-target open/half/
                                            │   closed; return 503 +
                                            │   Retry-After when open)
                                            │
                                            └─▶ actions.py
                                                (idempotent restart;
                                                 gated on ENABLE_REAL_
                                                 RECOVERY=true)

Public surface: ``router`` — a FastAPI APIRouter mounted under /admin/recovery
by main.py's admin-gated include block.
"""

from app.recovery.routes import router  # noqa: F401

__all__ = ["router"]
