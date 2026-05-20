"""Per-target circuit breaker for recovery operations.

State machine
─────────────

    closed ──fail count ≥ threshold──▶ open
       ▲                                │
       │                                │ (timer)
       │                                ▼
       └──── success ──────────────  half_open
                                       │
                                       └── fail ──▶ open

* **closed**     — requests pass through; failures increment a counter.
* **open**       — requests are rejected immediately with 503 + Retry-After.
                   After ``circuit_breaker_open_seconds`` the breaker
                   auto-transitions to half_open.
* **half_open**  — the next request is allowed through as a probe. Success
                   closes the breaker; failure re-opens it.

State lives in Redis under ``recovery:cb:<component>:<target_id>`` so the
breaker is shared across worker processes. Falls back to an in-memory
dict per process when Redis isn't available — same caveat as idempotency.

Why per-target, not per-endpoint:
   A camera that's physically broken (e.g. PoE switch port dead) shouldn't
   cause the breaker to also block restart attempts on other cameras.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Literal

from app.cache import redis_client
from app.config import settings

log = logging.getLogger(__name__)

_KEY_PREFIX = "recovery:cb:"
_INMEM: dict[str, dict] = {}

State = Literal["closed", "open", "half_open"]


def _key(component: str, target_id: str) -> str:
    return f"{_KEY_PREFIX}{component}:{target_id}"


async def _load(key: str) -> dict:
    c = await redis_client.get_client()
    if c is not None:
        try:
            raw = await c.get(key)
            if raw:
                return json.loads(raw)
        except Exception as e:
            log.debug("cb load failed (redis): %s; using in-memory", e)
    return _INMEM.get(key, {"state": "closed", "fail_count": 0, "opened_at": 0})


async def _store(key: str, payload: dict) -> None:
    c = await redis_client.get_client()
    if c is not None:
        try:
            # TTL = 2× open window so old state self-heals if traffic stops.
            await c.set(key, json.dumps(payload), ex=settings.circuit_breaker_open_seconds * 2)
            return
        except Exception as e:
            log.debug("cb store failed (redis): %s; using in-memory", e)
    _INMEM[key] = payload


async def check(component: str, target_id: str) -> tuple[bool, State, int]:
    """Check whether a recovery action against this target is allowed.

    Returns ``(allow, state, retry_after_seconds)``. ``retry_after`` is 0
    when the breaker is closed or half-open and rolls over to a positive
    number while open.
    """
    key = _key(component, target_id)
    payload = await _load(key)
    state: State = payload.get("state", "closed")
    fail = int(payload.get("fail_count", 0))
    opened_at = float(payload.get("opened_at", 0))

    if state == "open":
        elapsed = time.time() - opened_at
        if elapsed >= settings.circuit_breaker_open_seconds:
            # Timer expired — transition to half_open and allow the probe.
            payload["state"] = "half_open"
            await _store(key, payload)
            return True, "half_open", 0
        retry_after = int(settings.circuit_breaker_open_seconds - elapsed) or 1
        return False, "open", retry_after

    # closed or half_open both allow the request through.
    return True, state, 0


async def record_success(component: str, target_id: str) -> None:
    """Close the breaker on success; reset the failure counter."""
    key = _key(component, target_id)
    await _store(key, {"state": "closed", "fail_count": 0, "opened_at": 0})


async def record_failure(component: str, target_id: str) -> State:
    """Increment failure count; open the breaker if threshold crossed.

    Returns the new state so the caller can include it in the response.
    """
    key = _key(component, target_id)
    payload = await _load(key)
    fail = int(payload.get("fail_count", 0)) + 1
    state: State = payload.get("state", "closed")

    if state == "half_open":
        # Probe failed → re-open immediately.
        payload = {"state": "open", "fail_count": fail, "opened_at": time.time()}
    elif fail >= settings.circuit_breaker_failure_threshold:
        # Threshold reached → open.
        payload = {"state": "open", "fail_count": fail, "opened_at": time.time()}
    else:
        payload = {"state": "closed", "fail_count": fail, "opened_at": 0}

    await _store(key, payload)
    return payload["state"]
