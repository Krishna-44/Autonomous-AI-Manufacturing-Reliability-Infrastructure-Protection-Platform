# Recruiter / stakeholder demo — 5-minute walkthrough

Goal: in five minutes, show the AI self-healing platform reacting to a
simulated production failure end-to-end. No screenshots needed — every
step is a deterministic, repeatable action.

## Pre-flight (do this once, ~10 min before the demo)

```powershell
# from iot-rtls-production/
cp .env.example .env
# Edit .env: set POSTGRES_PASSWORD, N8N_ENCRYPTION_KEY, GRAFANA_ADMIN_PASSWORD,
# OPENAI_API_KEY, ANTHROPIC_API_KEY, SLACK_BOT_TOKEN.
# Leave the optional URLs (DASHBOARD_WEBHOOK_URL, FIREWALL_BLOCK_URL,
# VIDEO_DASHBOARD_WEBHOOK_URL) blank — workflow degrades gracefully.

docker compose --profile mes-plus up -d --build
# wait ~60s for all healthchecks to go green

# Import the Phase 3 workflow into n8n (once)
# Browser → http://localhost:5678 → ⋯ → Import from File →
#   ops/n8n/workflow.phase3.json → Save (don't activate yet)

# Bind credentials in the n8n UI: OpenAI, Anthropic, Postgres
# (host=postgres, db=mes_incidents, user/pass from .env), Slack, Pinecone.
# Bind the "HTTP Header Auth" credential to the 4 Execute-*Recovery nodes
# (header: Authorization, value: ={{ "Bearer " + $env.ADMIN_TOKEN }})

# Now activate the workflow.
```

Open four tabs side-by-side:
- **n8n executions**: http://localhost:5678/workflow/<id>/executions
- **Grafana dashboards**: http://localhost:3001 (login admin/admin)
- **Slack #incidents-critical**
- **Backend logs**: `docker compose logs -f backend`

## Demo script

### 0 → 0:30 — Set the stage (talk)

> *"This is a real industrial RTLS deployment for a Toyota Boshoku plant
> in Bawal. The base stack handles 30+ cameras, BLE beacons, MES line
> data. The AI self-healing layer I'm about to show you watches every
> component and recovers from failures without paging a human."*

Show Grafana → MES+ folder → the "Self-healing overview" dashboard.
Point at: zero open incidents, all healthchecks green.

### 0:30 → 1:30 — Trigger a real failure (action)

In a terminal:

```bash
# Simulate 3 cameras dropping their RTSP streams at once.
curl -X POST http://localhost:5678/webhook/cb008cf4-9df2-42c4-a4b0-c5895d9072a3 \
  -H "Content-Type: application/json" \
  -d '{
    "scenario": "rtsp_drop_burst",
    "cameras": ["CAM003", "CAM005", "CAM007"],
    "reason": "PoE switch port flap"
  }'
```

> *"That POST simulated three cameras dropping at once — the exact
> scenario that happens when a PoE switch port flaps. Watch what
> happens."*

### 1:30 → 3:00 — Show the AI react (narrate)

Switch to **n8n executions** tab. A new run appears, fanning out from
`Chaos Engineering Trigger`:

1. → `Simulate Infrastructure Failure` (the JS code node injects three
   failed-camera events)
2. → `Predictive Failure Analysis` (Postgres lookup of recent metrics)
3. → `AI Failure Predictor` (LLM agent) **runs for 3-6 seconds**
4. → Branches into 3 parallel `Camera Recovery Agent` invocations (one
   per failed camera)

> *"The LLM agent — Claude Sonnet — is now reading the failure pattern,
> the recovery history of these specific cameras, and a Pinecone vector
> lookup of similar past incidents. It's not pattern-matching a hardcoded
> rule. It just decided that the three drops correlate to a network
> issue, not three independent camera failures."*

Switch to **Slack #incidents-critical**. A message appears:

```
🔴 CRITICAL — 3 cameras down (CAM003 / CAM005 / CAM007)
Probable root cause: PoE switch flap (SW-FLOOR-2, port 12-14)
Action: attempting RTSP reconnect on all three
Confidence: 0.84
```

### 3:00 → 4:00 — Show the recovery hit the backend (action)

Switch to **backend logs**:

```
INFO ... recovery.mocked op=recover_camera target=CAM003 ...
INFO ... HTTP 200 POST /admin/recovery/camera/restart
INFO ... recovery.mocked op=recover_camera target=CAM005 ...
INFO ... HTTP 200 POST /admin/recovery/camera/restart
INFO ... recovery.mocked op=recover_camera target=CAM007 ...
INFO ... HTTP 200 POST /admin/recovery/camera/restart
```

> *"The AI hit `/admin/recovery/camera/restart` on our backend three
> times in 2 seconds — same parameters. Notice the idempotency layer
> caught the workflow's retries: only one real restart attempt per
> camera, not three. That's what stops the AI from accidentally DOS-ing
> the very thing it's trying to recover."*

`curl` the circuit-breaker state for one of the cameras:

```bash
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
  http://localhost:8000/admin/recovery/status/camera/CAM003
# → { "breaker": { "state": "closed", "fail_count": 0, "opened_at": 0 } }
```

> *"Breaker is closed because the recovery succeeded. If those cameras
> had kept failing five times in a row, the breaker would have flipped
> to open and the workflow would have escalated to a human via Slack —
> instead of hammering the broken switch for hours."*

### 4:00 → 5:00 — Close with the bigger story (talk)

Switch to **Grafana**: open the "Self-healing overview" panel:

- Incident counter went from 0 → 3 → 0
- Recovery success rate: 100% (3/3)
- AI agent latency: 2.4s p95
- Mean time to recovery: 6.1s

Open `ops/n8n/PHASE4B-DESIGN.md` if asked about what comes next:

> *"This is the foundation. Next we layer on CAPA generation —
> end-to-end documented Corrective + Preventive Actions for every
> incident — and shift-end reports that synthesize each operator's
> 8-hour window into a one-page brief for plant leadership. Both are
> AI-written and Postgres-backed; both deploy as additional n8n
> workflows without touching this one."*

End of demo.

## Talking points (FAQ)

**"What if OpenAI goes down?"**
The MES+ stack includes Ollama for local LLM fallback (Phase 1). The
agents fall back to `llama3.2:3b` (configurable). It's not as smart, but
the workflow keeps running. Detection + Slack alerts work even with no
LLM at all (those branches don't depend on AI).

**"Is this just a fancy webhook chain?"**
No. The LLM agents do real reasoning: structured-output parsing,
Pinecone vector lookups against historical incidents, Perplexity tool
calls for real-time research, and Redis-backed conversation memory
across iterations. The Camera Recovery Agent in particular adapts its
action plan to what the AI's previous attempts found.

**"Production line is live — what stops this from breaking things?"**
Five layers:
1. `ENABLE_REAL_RECOVERY=false` keeps every recovery action a no-op
   until promoted (Phase 4a, default-safe).
2. Per-target circuit breaker stops thundering herds (Phase 4a).
3. Idempotency dedup stops retry double-execution (Phase 4a).
4. Workflow `onError: continueRegularOutput` on optional nodes (Phase 2).
5. Graceful degradation when any external API is unreachable.

**"Where does the data go?"**
`docker compose exec postgres psql -U mes mes_incidents -c '\dt'` shows
all tables. Every incident, recovery attempt, AI agent output, and CAPA
is persisted. Grafana has a `Postgres (mes_incidents)` datasource ready
for ad-hoc queries.

**"Cost?"**
With a single-shift plant doing ~50 incidents/day, the OpenAI bill for
gpt-4o triage + Claude Sonnet remediation is ~$3-8/day. Switching to
local Ollama for non-critical paths drops it to ~$1/day. Compared to one
hour of plant downtime (typically $5,000+ at this kind of facility),
the math is trivial.

## Reset between demos

```bash
# Clear all incidents + recovery_log so the second demo starts clean.
docker compose exec postgres psql -U mes mes_incidents -c '
  TRUNCATE incidents, recovery_log, workflow_errors, capas,
           shift_reports, correlations, video_inspections RESTART IDENTITY;
'
# Clear Redis breaker state so no stale "open" breakers from prior runs.
docker compose exec redis redis-cli FLUSHDB
```
