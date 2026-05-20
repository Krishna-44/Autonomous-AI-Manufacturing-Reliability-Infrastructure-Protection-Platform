"""FastAPI routes mounted under /admin/recovery/.

One POST endpoint per component (process, camera, iframe, ffmpeg). Each
endpoint:

    1. Computes an idempotency key from (component, target_id, body).
    2. Returns the cached response if a request with the same key fired
       within ``recovery_idempotency_window_s``.
    3. Checks the per-target circuit breaker. If open → 503 + Retry-After.
    4. Calls into actions.py to perform the (gated, idempotent) recovery.
    5. Records success/failure on the breaker.
    6. Caches and returns the response.

All endpoints share the same response shape (``RecoveryResponse``) so the
workflow's downstream validation/logging nodes can treat them uniformly.

These routes are registered admin-gated by main.py via
``require_admin_for_write``, so an ADMIN_TOKEN is required when set.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Response

from app.recovery import actions, circuit_breaker, idempotency
from app.recovery.models import (
    CameraRecoveryRequest,
    FfmpegRecoveryRequest,
    IframeRecoveryRequest,
    ProcessRecoveryRequest,
    RecoveryResponse,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/recovery", tags=["mes-recovery"])


# ────────────────────────────────────────────────────────────────────────
# Shared pipeline. Each endpoint just plugs in (component, target_id, body)
# and the matching action function. This keeps the 4 routes thin and
# guarantees the idempotency / breaker / response logic is identical.
# ────────────────────────────────────────────────────────────────────────


async def _execute(
    *,
    component: str,
    target_id: str,
    body: dict[str, Any],
    action_fn,
    action_kwargs: dict[str, Any],
    response: Response,
) -> RecoveryResponse:
    request_id = str(uuid.uuid4())
    idem_key = idempotency.compute_key(component, target_id, body)

    # 1) Idempotency replay
    cached = await idempotency.get_cached(idem_key)
    if cached is not None:
        # Preserve original action_taken but flag the cache hit by overlay.
        cached_out = dict(cached)
        cached_out["action_taken"] = "deduped"
        cached_out["request_id"] = request_id
        return RecoveryResponse(**cached_out)

    # 2) Circuit breaker pre-check
    allow, state, retry_after = await circuit_breaker.check(component, target_id)
    if not allow:
        # 503 + Retry-After is the canonical "back off" signal for clients.
        response.status_code = 503
        response.headers["Retry-After"] = str(retry_after)
        return RecoveryResponse(
            ok=False,
            component=component,  # type: ignore[arg-type]
            target_id=target_id,
            action_taken="rejected_circuit_open",
            idempotency_key=idem_key,
            circuit_state=state,
            detail={"retry_after_seconds": retry_after, "message": "circuit breaker open; not attempting recovery"},
            request_id=request_id,
        )

    # 3) Perform the action (only the kwargs the action signature accepts)
    ok, detail = await action_fn(**action_kwargs)

    # 4) Record on the breaker
    if ok:
        await circuit_breaker.record_success(component, target_id)
        new_state = "closed"
    else:
        new_state = await circuit_breaker.record_failure(component, target_id)

    # 5) Compose response, cache, return
    out = RecoveryResponse(
        ok=ok,
        component=component,  # type: ignore[arg-type]
        target_id=target_id,
        action_taken=("executed" if (ok and detail and not detail.get("mocked")) else ("mocked" if (detail and detail.get("mocked")) else ("failed" if not ok else "executed"))),
        idempotency_key=idem_key,
        circuit_state=new_state,  # type: ignore[arg-type]
        detail=detail,
        request_id=request_id,
    )
    await idempotency.put(idem_key, out.model_dump())
    return out


# ────────────────────────────────────────────────────────────────────────
# 4 endpoints — one per component. Bodies match the workflow's
# Execute-* node payloads.
# ────────────────────────────────────────────────────────────────────────


@router.post("/process/restart", response_model=RecoveryResponse)
async def process_restart(req: ProcessRecoveryRequest, response: Response):
    body = req.model_dump(exclude_none=True)
    return await _execute(
        component="process",
        target_id=req.process_name,
        body=body,
        action_fn=actions.restart_process,
        action_kwargs={
            "process_name": req.process_name,
            "recovery_steps": req.recovery_steps,
            "reason": req.reason,
        },
        response=response,
    )


@router.post("/camera/restart", response_model=RecoveryResponse)
async def camera_restart(req: CameraRecoveryRequest, response: Response):
    body = req.model_dump(exclude_none=True)
    return await _execute(
        component="camera",
        target_id=req.camera_id,
        body=body,
        action_fn=actions.recover_camera,
        action_kwargs={
            "camera_id": req.camera_id,
            "action": req.action,
            "recovery_steps": req.recovery_steps,
        },
        response=response,
    )


@router.post("/iframe/refresh", response_model=RecoveryResponse)
async def iframe_refresh(req: IframeRecoveryRequest, response: Response):
    body = req.model_dump(exclude_none=True)
    return await _execute(
        component="iframe",
        target_id=req.iframe_id,
        body=body,
        action_fn=actions.refresh_iframe,
        action_kwargs={
            "iframe_id": req.iframe_id,
            "action": req.action,
        },
        response=response,
    )


@router.post("/ffmpeg/restart", response_model=RecoveryResponse)
async def ffmpeg_restart(req: FfmpegRecoveryRequest, response: Response):
    body = req.model_dump(exclude_none=True)
    return await _execute(
        component="ffmpeg",
        target_id=req.recorder_id,
        body=body,
        action_fn=actions.restart_ffmpeg,
        action_kwargs={
            "recorder_id": req.recorder_id,
            "action": req.action,
            "recovery_steps": req.recovery_steps,
        },
        response=response,
    )


# Diagnostic GET — useful for the dashboard and for the n8n workflow's
# Periodic Health Check node to peek at breaker state without triggering
# a recovery action.
@router.get("/status/{component}/{target_id}")
async def status(component: str, target_id: str):
    state_pack = await circuit_breaker._load(circuit_breaker._key(component, target_id))  # noqa: SLF001
    return {
        "component": component,
        "target_id": target_id,
        "breaker": state_pack,
    }
