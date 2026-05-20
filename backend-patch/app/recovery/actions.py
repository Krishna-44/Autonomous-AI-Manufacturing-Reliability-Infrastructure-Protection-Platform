"""Recovery actions per component.

Each function returns ``(ok: bool, detail: dict)``. When
``settings.enable_real_recovery`` is False (the default), every function
short-circuits to a ``mocked`` result — the workflow's end-to-end flow runs
exactly as in production but no destructive operation actually happens.

**Idempotency contract** — every real-path implementation MUST be
idempotent. The workflow's Phase 3 retry policy means the same payload may
arrive up to 3× in ~2 s (the first-call idempotency dedup catches most of
those, but not all — e.g. when Redis is briefly unreachable). A second
restart of an already-healthy service is a no-op and returns
``{"already_healthy": true}``; that's a success, not a failure.

When you implement real restart logic, follow the pattern:

    1. Check current state of the target.
    2. If already healthy → return ``(True, {"already_healthy": True})``.
    3. Otherwise perform the minimal recovery action (don't bounce more
       than the target).
    4. Briefly re-check; return ``(True, {...})`` or ``(False, {...})``.

Never raise — wrap exceptions and return ``(False, {"error": str(e)})``
so the circuit breaker can record the failure correctly.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import settings

log = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────
# Component-level recovery functions.
#
# The stub implementations below log the intended action and return a
# ``mocked`` detail when ENABLE_REAL_RECOVERY is False. Replace the
# ``# TODO real-impl`` blocks with calls into the existing managers
# (app.camera.manager.CameraManager, app.ingestion.*, etc.) once you've
# verified the idempotency contract for each.
# ────────────────────────────────────────────────────────────────────────


def _mocked(op: str, target: str, **extra: Any) -> tuple[bool, dict[str, Any]]:
    log.info("recovery.mocked op=%s target=%s extras=%s", op, target, extra)
    return True, {"mocked": True, "would_perform": op, "target": target, **extra}


async def restart_process(process_name: str, recovery_steps: list[str] | None = None, reason: str | None = None) -> tuple[bool, dict[str, Any]]:
    if not settings.enable_real_recovery:
        return _mocked("restart_process", process_name, recovery_steps=recovery_steps, reason=reason)

    # TODO real-impl: integrate with the platform's process supervisor.
    # On Windows installs the platform uses NSSM (see scripts/install-
    # windows-services.ps1 and fix-nssm-args*.ps1). On Linux it would
    # be systemd. Map ``process_name`` to a supervisor unit and call
    # ``nssm restart <unit>`` / ``systemctl restart <unit>``.
    log.warning("restart_process(%s) called with ENABLE_REAL_RECOVERY=true but no real impl wired", process_name)
    return False, {"error": "real-impl not wired", "process_name": process_name}


async def recover_camera(camera_id: str, action: str = "reconnect", recovery_steps: list[str] | None = None) -> tuple[bool, dict[str, Any]]:
    if not settings.enable_real_recovery:
        return _mocked("recover_camera", camera_id, action=action, recovery_steps=recovery_steps)

    # TODO real-impl: call into app.camera.manager.CameraManager which is
    # the singleton set via app.camera.runtime.set_camera_manager() and
    # exposed as app.camera.runtime.camera_manager.
    # Typical recovery: manager.get(camera_id).reconnect() (idempotent).
    try:
        from app.camera import runtime as camera_runtime  # local to avoid import cycle
        mgr = camera_runtime.camera_manager
        if mgr is None:
            return False, {"error": "camera manager not initialised", "camera_id": camera_id}
        # Implementations of the actual recovery method should live on the
        # manager; until they're added we return a "not wired" failure so
        # the circuit breaker can track it correctly.
        recover = getattr(mgr, "recover_camera", None)
        if recover is None:
            log.warning("CameraManager.recover_camera not implemented; please add an idempotent method")
            return False, {"error": "recover_camera not implemented on CameraManager", "camera_id": camera_id}
        result = await recover(camera_id, action=action) if callable(recover) else {"called": False}
        return True, {"result": result, "camera_id": camera_id, "action": action}
    except Exception as e:  # never raise; let CB record failure
        log.exception("recover_camera failed: %s", e)
        return False, {"error": str(e), "camera_id": camera_id}


async def refresh_iframe(iframe_id: str, action: str = "refresh") -> tuple[bool, dict[str, Any]]:
    if not settings.enable_real_recovery:
        return _mocked("refresh_iframe", iframe_id, action=action)

    # Iframe refresh is a frontend concern. We publish a Redis pubsub
    # message; the frontend's WebSocket bridge picks it up and triggers a
    # location.reload() on the matching iframe.
    try:
        from app.cache import redis_client
        c = await redis_client.get_client()
        msg = {"type": "iframe_recovery", "iframe_id": iframe_id, "action": action}
        if c is not None:
            await c.publish("recovery.iframe", __import__("json").dumps(msg))
            return True, {"published": True, "channel": "recovery.iframe", "message": msg}
        # No Redis → emit a structured log line the frontend's polling can
        # see via the existing /system-health/events feed.
        log.info("recovery.iframe (no redis fallback) %s", msg)
        return True, {"published": False, "reason": "redis-unavailable", "message": msg}
    except Exception as e:
        log.exception("refresh_iframe failed: %s", e)
        return False, {"error": str(e), "iframe_id": iframe_id}


async def restart_ffmpeg(recorder_id: str, action: str = "restart", recovery_steps: list[str] | None = None) -> tuple[bool, dict[str, Any]]:
    if not settings.enable_real_recovery:
        return _mocked("restart_ffmpeg", recorder_id, action=action, recovery_steps=recovery_steps)

    # TODO real-impl: app.camera.rtsp_ingest manages the ffmpeg subprocess
    # per camera. Add a ``restart_recorder(camera_id)`` method there that
    # SIGTERM's the existing ffmpeg pid (with a SIGKILL fallback after
    # 5 s) and reopens a fresh stream. The method must be idempotent —
    # if the recorder is already running and healthy, return without
    # bouncing it.
    log.warning("restart_ffmpeg(%s) called with ENABLE_REAL_RECOVERY=true but no real impl wired", recorder_id)
    return False, {"error": "real-impl not wired", "recorder_id": recorder_id}
