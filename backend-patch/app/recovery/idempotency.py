"""Recovery-request deduplication.

The n8n workflow's HTTP nodes retry up to 3× with 500 ms backoff (Phase 3).
Without dedup we'd execute the same restart 3× in ~2 s, which for stateful
actions (ffmpeg respawn, camera RTSP reconnect) is at best wasteful and at
worst destabilizing.

We hash the (component, target_id, action, body_digest) tuple to derive a
key, then store the first successful response in Redis with a TTL of
``settings.recovery_idempotency_window_s`` seconds. Repeat requests within
the window return the cached response — the caller still gets a 200, but
no real work happens.

Falls back to an in-memory dict when Redis isn't available. The in-memory
fallback is per-process so it won't dedupe across workers in a multi-worker
deployment — that's a known limitation; flip ``redis_enabled=true`` in
config to get cross-worker dedup.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from app.cache import redis_client
from app.config import settings

log = logging.getLogger(__name__)

_KEY_PREFIX = "recovery:idem:"
_INMEM: dict[str, tuple[float, dict[str, Any]]] = {}


def compute_key(component: str, target_id: str, body: dict[str, Any]) -> str:
    """Stable hash of the request shape — same input → same key."""
    canonical = json.dumps(
        {"c": component, "t": target_id, "b": body},
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return f"{_KEY_PREFIX}{component}:{target_id}:{digest}"


async def get_cached(key: str) -> dict[str, Any] | None:
    """Return the cached response if a request with this key fired recently."""
    c = await redis_client.get_client()
    if c is not None:
        try:
            raw = await c.get(key)
            if raw:
                return json.loads(raw)
        except Exception as e:
            log.debug("idem get failed (redis): %s; falling back to in-memory", e)
    # In-memory fallback
    now = time.time()
    entry = _INMEM.get(key)
    if not entry:
        return None
    expires_at, payload = entry
    if expires_at < now:
        _INMEM.pop(key, None)
        return None
    return payload


async def put(key: str, response: dict[str, Any]) -> None:
    """Cache the response for the idempotency window."""
    ttl = settings.recovery_idempotency_window_s
    c = await redis_client.get_client()
    if c is not None:
        try:
            await c.set(key, json.dumps(response), ex=ttl)
            return
        except Exception as e:
            log.debug("idem put failed (redis): %s; falling back to in-memory", e)
    # In-memory fallback
    _INMEM[key] = (time.time() + ttl, response)
    # Best-effort prune so the dict doesn't grow unbounded.
    if len(_INMEM) > 1024:
        now = time.time()
        for k in list(_INMEM.keys()):
            if _INMEM[k][0] < now:
                _INMEM.pop(k, None)
