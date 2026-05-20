# backend-patch — drop-in FastAPI recovery routes

This directory contains the **server-side half** of the AI self-healing
loop: the four `/admin/recovery/*` routes that the n8n workflow POSTs
to when it decides a component needs to be restarted.

The code lives here as a self-contained Python package so you can copy
it straight into your MES backend without touching anything else.

## What it provides

```
POST /admin/recovery/process/restart   Restart a named platform process
POST /admin/recovery/camera/restart    Reconnect / restart an RTSP camera
POST /admin/recovery/iframe/refresh    Publish frontend iframe-reload pubsub
POST /admin/recovery/ffmpeg/restart    Respawn the ffmpeg recorder for a camera
GET  /admin/recovery/status/{c}/{id}   Diagnostic — read circuit-breaker state
```

Every POST runs through the same pipeline:

1. **Idempotency dedup** — Redis-backed (in-memory fallback). The
   workflow's Phase-3 retry policy means the same payload may arrive
   3× in ~2 s; dedup ensures the actual restart fires exactly once.
2. **Per-target circuit breaker** — Redis-backed. After N consecutive
   failures on the same target, that target gets 503 + Retry-After
   for a configurable window. Other targets stay healthy.
3. **Action execution** — gated by `ENABLE_REAL_RECOVERY` env var.
   Default is `false`, in which case actions log the requested
   operation and return `action_taken: "mocked"`. **No destructive
   work happens until you explicitly promote.**
4. **State update** — success closes the breaker; failure increments
   the counter and may flip the state to open.

Response shape (same for all 4 endpoints):

```json
{
  "ok": true,
  "component": "camera",
  "target_id": "CAM001",
  "action_taken": "executed",
  "idempotency_key": "recovery:idem:camera:CAM001:abc...",
  "circuit_state": "closed",
  "detail": { "result": ... },
  "request_id": "uuid"
}
```

## Integration (2 steps)

### 1. Copy the package into your FastAPI app

```bash
cp -r backend-patch/app/recovery <your-mes-backend>/app/
```

### 2. Wire the router + settings

In your FastAPI app's `main.py`:

```python
from app.recovery import router as recovery_router
# admin_gate is your existing auth dependency (we use `require_admin_for_write`)
app.include_router(recovery_router, dependencies=[Depends(admin_gate)])
```

In your `Settings` (pydantic-settings):

```python
class Settings(BaseSettings):
    # ... your existing fields ...

    # MES recovery (phase 4a)
    enable_real_recovery: bool = False
    circuit_breaker_open_seconds: int = 60
    circuit_breaker_failure_threshold: int = 5
    recovery_idempotency_window_s: int = 30
    escalate_maintenance: str = ""
    escalate_software_engineer: str = ""
    escalate_supervisor: str = ""
    escalate_admin: str = ""
```

That's it. The package imports lazily from `app.cache.redis_client` —
adapt the import in `idempotency.py` and `circuit_breaker.py` to your
own Redis client wrapper if your project's path differs. The package
falls back to in-memory state if Redis isn't reachable so dev runs
without Redis still work (single-process dedup only).

## Smoke test

```bash
# Mock-safe call (default state):
curl -X POST http://localhost:8000/admin/recovery/camera/restart \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"camera_id":"CAM001","action":"reconnect"}'
# Expect: 200, action_taken="mocked"

# Diagnostic:
curl http://localhost:8000/admin/recovery/status/camera/CAM001
# Expect: { breaker: { state: "closed", fail_count: 0, opened_at: 0 } }
```

## Promoting to real recovery

`actions.py` contains four functions with `# TODO real-impl` markers:

| Action | Where to wire |
|---|---|
| `restart_process` | NSSM (Windows) or systemd (Linux) — map process names to supervisor units |
| `recover_camera` | Add `recover_camera(camera_id, action)` to your CameraManager — must be idempotent |
| `refresh_iframe` | **Already wired** — publishes to `recovery.iframe` Redis pubsub. Frontend WS bridge needs a 5-line subscriber |
| `restart_ffmpeg` | Add `restart_recorder(recorder_id)` to your rtsp_ingest module — SIGTERM + 5 s grace + SIGKILL fallback |

Each real-impl is a focused 20-40 line patch. Once wired, flip
`ENABLE_REAL_RECOVERY=true` in your `.env` and restart your backend.
The endpoints' response will switch from `action_taken: "mocked"` to
`"executed"` and actual restarts will happen.

## Architecture

This package implements one side of the self-healing loop. The other
side — n8n agents that decide *what* to recover and *when* — lives
in the workflow at [`../ops/n8n/workflow.phase3.json`](../ops/n8n/workflow.phase3.json).

Full sequence diagram: [`../ops/ARCHITECTURE.md`](../ops/ARCHITECTURE.md) §3.
