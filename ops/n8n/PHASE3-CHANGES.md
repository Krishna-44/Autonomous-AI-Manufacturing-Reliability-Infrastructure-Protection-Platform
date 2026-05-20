# Phase 3 — retry, timeout, workflow settings hardening

Applied to: `AI Incident Intelligence & Self-Healing Platform copy`
(n8n cloud, ID `6bCvdspAVAJsw6Z4`)

`workflow.phase3.json` is **cumulative**: it contains every Phase 2 edit
plus the Phase 3 additions. Import this file as a one-shot replacement.
(`workflow.phase2.json` is no longer needed and may be deleted.)

## How to apply

1. Open n8n → Workflows → `AI Incident Intelligence & Self-Healing Platform copy`.
2. ⋯ → **Import from File** → pick `workflow.phase3.json`.
3. **Save**. Leave the workflow inactive (Phase 4 backend isn't ready yet).
4. No new env vars are needed for Phase 3.

## Rollback

Re-import `workflow.original.json` (still in this folder).

## What Phase 3 adds (10 changes)

### 1. Node-level retry on all 8 HTTP nodes (8 changes)

| Node | Retry? | Tries | Wait | Timeout |
|---|---|---|---|---|
| Fetch Prometheus Metrics    | ✓ | 3 | 500 ms | 10 s |
| Push To Dashboard Webhook   | ✓ | 3 | 500 ms | 10 s |
| Block Malicious IPs         | ✓ | 3 | 500 ms | 10 s |
| Push Video Health Dashboard | ✓ | 3 | 500 ms | 10 s |
| Execute Process Recovery    | ✓ | 3 | 500 ms | **30 s** |
| Execute Camera Recovery     | ✓ | 3 | 500 ms | **30 s** |
| Execute Iframe Recovery     | ✓ | 3 | 500 ms | **30 s** |
| Execute Ffmpeg Recovery     | ✓ | 3 | 500 ms | **30 s** |

**Why 30 s for recovery POSTs:** restart actions can legitimately take 10–25 s
(ffmpeg respawn, RTSP reconnect handshake, gunicorn worker boot). A 10 s
timeout would incorrectly classify slow-but-successful recoveries as failures
and trigger duplicate restart attempts on retry.

**Idempotency note:** The four `Execute *Recovery` endpoints are POSTs that
retry up to 3× on failure. The Phase 4 implementation MUST make them
idempotent (restarting an already-healthy service is a no-op), otherwise
retries could cause double-restart cascades. This is documented in the
recovery router contract — see `backend/app/recovery/` (added in Phase 4).

### 2. workflow.settings hardening (1 change touching 7 keys)

| Setting | Old | New | Why |
|---|---|---|---|
| timezone | _(unset)_ | `Asia/Kolkata` | Daily Summary at 9 AM IST, not 9 AM UTC |
| executionTimeout | _(unset)_ | `300` (5 min) | A stuck workflow gets killed instead of pinning a worker forever |
| saveDataErrorExecution | _(default)_ | `all` | Keep full input/output for failed runs — needed for debugging |
| saveDataSuccessExecution | _(default)_ | `none` | Don't store data on successful runs — saves Postgres space |
| saveManualExecutions | _(default)_ | `true` | Manual test runs still get logged |
| saveExecutionProgress | _(unset)_ | `false` | Per-node checkpointing is expensive; off in production |
| executionOrder | `v1` | `v1` | Unchanged — already correct |
| binaryMode | `separate` | `separate` | Unchanged |
| availableInMCP | `true` | `true` | Unchanged |

### 3. Circuit breakers — deliberately deferred to Phase 4

The original spec called for circuit breakers. After re-reading the
architecture, circuit-breaker logic belongs **in the FastAPI backend**, not
in the workflow:

- The workflow calls 4 recovery endpoints that the backend implements.
- If a target service (e.g. a specific camera) repeatedly fails to restart,
  the *backend* opens a circuit on that target — returning fast 503 with
  `Retry-After` for the next 60 s.
- The workflow's retry hits that 503 quickly, exhausts its 3 tries, and
  bubbles up to the existing `Error Recovery Trigger` → `Log Workflow
  Error` → `Alert On Workflow Failure` chain (which then escalates).

Putting the breaker in the backend keeps state in one place (Redis, owned
by the backend), avoids 12+ new workflow nodes, and lets the same logic
protect non-workflow callers too (manual dashboard buttons, future REST
clients). Phase 4 ships this.

## Verification (already run)

- 113 / 113 nodes preserved (no adds/removes/renames)
- 97 / 97 connection groups preserved
- 0 placeholder tokens remaining
- 6 distinct `$env.*` references
- 11 / 11 schedules have explicit intervals
- 8 / 8 HTTP nodes have retry+timeout
- 3 / 3 optional-target nodes keep `onError: continueRegularOutput`

## What's next

**Phase 4 (backend):** add the four `/admin/recovery/*` routes to
`backend/`, with idempotent restart logic, Redis-backed circuit breakers,
request throttling, connection pooling for outbound calls, and the
escalation matrix. Plus AI predictive maintenance / CAPA / shift
intelligence subworkflows.

**Phase 5:** docs, architecture diagrams, recruiter demo mode.
