"""Pydantic request/response models for the recovery endpoints.

Shape matches what the n8n workflow's `Execute *Recovery` HTTP nodes POST.
Look at the workflow's `jsonBody` parameter on each Execute-* node to see the
exact payload — these models mirror it 1:1 plus a few optional fields.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ────────────────────────────────────────────────────────────────────────
# Request models — one per component. Kept separate so each can grow
# independently as the recovery semantics for that component evolve.
# ────────────────────────────────────────────────────────────────────────


class ProcessRecoveryRequest(BaseModel):
    process_name: str = Field(..., description="Logical name (e.g. 'rtls-ingest', 'fusion-worker').")
    recovery_steps: list[str] | None = Field(default=None, description="Optional ordered list of remediation steps from the AI agent.")
    reason: str | None = Field(default=None, description="Free-text reason / triggering symptom.")
    severity: Literal["critical", "high", "medium", "low"] | None = None


class CameraRecoveryRequest(BaseModel):
    camera_id: str = Field(..., description="Camera registry id (e.g. 'CAM001').")
    action: Literal["reconnect", "restart_stream", "reset_pipeline", "noop"] = Field(default="reconnect")
    recovery_steps: list[str] | None = None
    reason: str | None = None


class IframeRecoveryRequest(BaseModel):
    iframe_id: str = Field(..., description="Dashboard iframe id; published as a Redis pubsub message for the frontend to act on.")
    action: Literal["refresh", "reload", "noop"] = Field(default="refresh")
    reason: str | None = None


class FfmpegRecoveryRequest(BaseModel):
    recorder_id: str = Field(..., description="Ffmpeg recorder identifier (typically the camera_id it transcodes for).")
    action: Literal["restart", "respawn", "noop"] = Field(default="restart")
    recovery_steps: list[str] | None = None
    reason: str | None = None


# ────────────────────────────────────────────────────────────────────────
# Response model — same shape for all 4 endpoints so the workflow's
# downstream validation / logging nodes can treat them uniformly.
# ────────────────────────────────────────────────────────────────────────


class RecoveryResponse(BaseModel):
    ok: bool
    component: Literal["process", "camera", "iframe", "ffmpeg"]
    target_id: str
    action_taken: str
    """One of: 'executed', 'mocked' (when ENABLE_REAL_RECOVERY=false),
    'deduped' (idempotency hit), 'rejected_circuit_open'."""
    idempotency_key: str
    circuit_state: Literal["closed", "open", "half_open"]
    detail: dict[str, Any] | None = None
    request_id: str
    """UUID4 — opaque request identifier the workflow can log against."""
