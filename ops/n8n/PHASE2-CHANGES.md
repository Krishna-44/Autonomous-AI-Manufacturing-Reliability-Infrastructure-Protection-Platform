# Phase 2 — workflow env-var hardening

Applied to: `AI Incident Intelligence & Self-Healing Platform copy`
(n8n cloud, ID `6bCvdspAVAJsw6Z4`)

## Files in this folder

| File | Purpose |
|---|---|
| `workflow.original.json` | Pre-Phase-2 snapshot. Rollback by importing this. |
| `workflow.phase2.json`   | Phase-2 corrected workflow. Import this. |
| `PHASE2-CHANGES.md`      | This file. |

## How to apply

1. Open n8n at `https://proxusss.app.n8n.cloud/`.
2. Workflows → open `AI Incident Intelligence & Self-Healing Platform copy`.
3. Top-right ⋯ menu → **Import from File** → pick `workflow.phase2.json`.
4. Click **Save** (top right). The workflow stays *inactive* — that's
   intentional. Activation happens after Phase 3 (retry/circuit-breaker)
   and Phase 4 (real recovery endpoints) land.
5. Set the env vars in the n8n project: Settings → Variables → add the 6
   vars below (or set them on the n8n container's `.env` if running
   self-hosted via the Phase 1 docker-compose `mes-plus` profile):

   | Variable | Required? | Notes |
   |---|---|---|
   | `PROMETHEUS_URL` | yes (if Periodic Health Check is active) | `http://prometheus:9090/api/v1/query` for self-host, your URL for cloud |
   | `MES_API_BASE` | yes (4 recovery nodes use it) | `http://backend:8000` for self-host |
   | `OPS_LEADERSHIP_EMAIL` | yes (Daily Summary uses it) | comma-separated recipients |
   | `DASHBOARD_WEBHOOK_URL` | optional | blank → workflow continues, push is no-op |
   | `FIREWALL_BLOCK_URL` | optional | blank → IP-block becomes log-only |
   | `VIDEO_DASHBOARD_WEBHOOK_URL` | optional | blank → continues |

## Rollback

If anything looks wrong after import:

1. Workflows → open the same workflow.
2. ⋯ menu → **Import from File** → pick `workflow.original.json`.
3. Save.

The original workflow (`AI Incident Intelligence & Self-Healing Platform`,
ID `2AgU0EcXHaVHscYa`) is also untouched and serves as a second rollback.

## Changes applied (18 total)

### 1. Placeholder URLs → `$env` references (9)

| Node | Field | New value |
|---|---|---|
| Fetch Prometheus Metrics | url | `={{ $env.PROMETHEUS_URL }}` |
| Email Daily Summary | sendTo | `={{ $env.OPS_LEADERSHIP_EMAIL }}` |
| Push To Dashboard Webhook | url | `={{ $env.DASHBOARD_WEBHOOK_URL }}` |
| Block Malicious IPs | url | `={{ $env.FIREWALL_BLOCK_URL }}` |
| Execute Process Recovery | url | `={{ $env.MES_API_BASE }}/admin/recovery/process/restart` |
| Execute Camera Recovery | url | `={{ $env.MES_API_BASE }}/admin/recovery/camera/restart` |
| Execute Iframe Recovery | url | `={{ $env.MES_API_BASE }}/admin/recovery/iframe/refresh` |
| Execute Ffmpeg Recovery | url | `={{ $env.MES_API_BASE }}/admin/recovery/ffmpeg/restart` |
| Push Video Health Dashboard | url | `={{ $env.VIDEO_DASHBOARD_WEBHOOK_URL }}` |

### 2. Schedule fixes (6) — the dangerous ones first

| Node | Was | Now | Reason |
|---|---|---|---|
| **Periodic Health Check** | `seconds`, no interval | every **30 s** | Previously fired every 1 s — would hammer Prometheus |
| **Process Watchdog Monitor** | `seconds`, no interval | every **30 s** | Same problem |
| **DB Health Monitor** | `seconds`, no interval | every **1 min** | Avoid spamming Postgres with health checks |
| GitHub Deployment Monitor | `minutes`, no interval | every **5 min** | Make explicit |
| Predictive Breakdown Monitor | `hours`, no interval | every **1 h** | Make explicit |
| Video Health Dashboard Schedule | `minutes`, no interval | every **5 min** | Make explicit |

Unchanged schedules: Daily Summary (9 AM IST), Traffic Overload (10 s),
Camera Health (2 min), Iframe Health (15 s), Ffmpeg Recorder (20 s).

### 3. Graceful-degradation flags (3)

These three HTTP nodes target *optional* env vars (`DASHBOARD_WEBHOOK_URL`,
`FIREWALL_BLOCK_URL`, `VIDEO_DASHBOARD_WEBHOOK_URL`). If the var is blank
or the target is unreachable, the workflow now continues instead of
halting. Set on the node: `onError = continueRegularOutput`.

| Node | Why optional |
|---|---|
| Push To Dashboard Webhook | Dashboard push is observability, not load-bearing |
| Block Malicious IPs | Firewall may not be wired in early deploys |
| Push Video Health Dashboard | Same — observability |

For the four `Execute * Recovery` nodes, error handling stays at the
default (`stopWorkflow`) so a recovery failure is *visible*, not silent.
Phase 3 wraps them with retry/circuit-breaker logic so transient
failures still self-heal.

## Verification (already run)

- Node count preserved: **113 → 113**
- Connection groups preserved: **97 → 97**
- Placeholder tokens remaining: **9 → 0**
- All 11 schedule triggers now have explicit intervals.
- 6 distinct `$env.*` references in node parameters.

## Not done in Phase 2 (deferred)

- Retry/backoff on the 8 HTTP nodes — **Phase 3**
- Circuit breaker (Redis-backed) — **Phase 3**
- Real subprocess restart endpoints in FastAPI backend — **Phase 4**
- AI predictive maintenance / CAPA / shift-intelligence subworkflows — **Phase 4**
- Architecture diagrams + recruiter demo mode — **Phase 5**
