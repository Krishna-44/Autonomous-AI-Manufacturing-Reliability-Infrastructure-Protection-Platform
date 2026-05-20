# Phase 4a — Backend MES recovery endpoints

This phase adds the four `/admin/recovery/*` routes that the n8n workflow's
`Execute *Recovery` HTTP nodes (phase-3) call into, plus the supporting
infrastructure (idempotency, circuit breaker, mock-safe gating).

Unlike Phase 1-3, this phase touches the **backend code**, not the workflow.
There is no `workflow.phase4a.json` to import.

## Endpoints added

| Path | Method | Body | Purpose |
|---|---|---|---|
| `/admin/recovery/process/restart` | POST | `ProcessRecoveryRequest` | Restart a named platform process |
| `/admin/recovery/camera/restart`  | POST | `CameraRecoveryRequest`  | Reconnect / restart an RTSP camera |
| `/admin/recovery/iframe/refresh`  | POST | `IframeRecoveryRequest`  | Publish a frontend iframe-reload pub/sub |
| `/admin/recovery/ffmpeg/restart`  | POST | `FfmpegRecoveryRequest`  | Respawn the ffmpeg recorder for a camera |
| `/admin/recovery/status/{c}/{id}` | GET  | _(none)_ | Diagnostic — read circuit-breaker state |

All POST routes share the same response shape (`RecoveryResponse`) so the
workflow's downstream nodes treat them uniformly:

```json
{
  "ok": true,
  "component": "camera",
  "target_id": "CAM001",
  "action_taken": "mocked",          // executed | mocked | deduped | rejected_circuit_open | failed
  "idempotency_key": "recovery:idem:camera:CAM001:1c3403dd...",
  "circuit_state": "closed",         // closed | open | half_open
  "detail": { ... },
  "request_id": "779bc9d2-..."
}
```

## Files added / changed

| File | Change |
|---|---|
| `backend/app/config.py` | +5 new settings (enable_real_recovery, breaker thresholds, idempotency window, escalation matrix) |
| `backend/app/recovery/__init__.py` | New — exports the router |
| `backend/app/recovery/models.py` | New — Pydantic request/response models |
| `backend/app/recovery/idempotency.py` | New — Redis-backed request dedup with in-memory fallback |
| `backend/app/recovery/circuit_breaker.py` | New — per-target Redis-backed breaker state machine |
| `backend/app/recovery/actions.py` | New — gated action functions (mock-safe by default) |
| `backend/app/recovery/routes.py` | New — 4 POST endpoints + diagnostic GET |
| `backend/app/main.py` | +5 lines — admin-gated `include_router(recovery_router)` |

## Verified

- `from app.main import app` imports cleanly.
- All 5 routes registered (4 POST + 1 GET).
- Functional pytest-style run via TestClient:
  - 200 OK on all 4 mock-safe POSTs
  - `action_taken: mocked` (because ENABLE_REAL_RECOVERY=false)
  - Idempotency dedup confirmed: identical second POST returns `action_taken: deduped`
  - Circuit breaker opens after threshold failures, closes on success
  - Per-target isolation: failing camera A doesn't open breaker for camera B

## Safety guarantees (because production line is LIVE)

1. **Mock-safe by default.** `enable_real_recovery=False` ships. All
   endpoints log + return 200 + `action_taken: mocked` without touching
   anything. Promote with `ENABLE_REAL_RECOVERY=true` only after reviewing
   `actions.py` and wiring real implementations into the `TODO real-impl`
   blocks.
2. **Idempotency dedup** absorbs the workflow's Phase 3 retry storm — the
   first request within a 30 s window executes; duplicates return the
   cached response.
3. **Per-target circuit breaker** stops thundering herds. After 5
   consecutive failures on the *same target_id*, that target gets 503
   responses for 60 s. Other targets keep working.
4. **Half-open probe** — after the open window expires, the next request
   is allowed through. Success closes the breaker; failure re-opens.
5. **Redis-backed when available, in-memory when not.** No hard
   dependency. Single-node dev runs without Redis still get dedup +
   breaker behavior; multi-worker prod uses Redis for cross-process
   state.
6. **Auth gated.** Routes are registered under `require_admin_for_write`.
   When `ADMIN_TOKEN` is set, workflow callers must include
   `Authorization: Bearer $ADMIN_TOKEN`. The n8n workflow's HTTP nodes
   already have `authentication: genericCredentialType / httpHeaderAuth`
   configured — you'll need to create an n8n "Header Auth" credential
   with header name `Authorization` and value `Bearer $env.ADMIN_TOKEN`
   and bind it to all 4 `Execute *Recovery` nodes. (One-time UI step.)

## What's NOT done in Phase 4a (deferred)

The real restart implementations (the `# TODO real-impl` blocks):

| Action | Where to wire | Notes |
|---|---|---|
| `restart_process` | NSSM (Win) / systemd (Linux) | Map process_name → unit. Idempotent restart only. |
| `recover_camera` | `app.camera.manager.CameraManager.recover_camera()` | Method needs adding; idempotent reconnect. |
| `refresh_iframe` | Already wired — publishes to `recovery.iframe` Redis pubsub. Frontend WS bridge needs a subscriber (5 lines). |
| `restart_ffmpeg` | `app.camera.rtsp_ingest` — add `restart_recorder(id)` | SIGTERM (5 s grace) → SIGKILL → respawn. |

Each is a focused 20-40 line patch. Quick to do once you've decided the
exact semantics for each component. Until then, the workflow runs
end-to-end with mocked actions — proving the wiring + observability
without touching production processes.

## What's next

**Phase 4b** — new AI workflow subsystems (predictive maintenance, CAPA
generation, shift intelligence, AI root-cause correlator). These add new
nodes to the n8n workflow (back to the JSON-export pattern) and consume
the data already flowing into Postgres + Redis.

**Phase 5** — architecture diagrams, recruiter demo mode, GitHub-ready
README.
