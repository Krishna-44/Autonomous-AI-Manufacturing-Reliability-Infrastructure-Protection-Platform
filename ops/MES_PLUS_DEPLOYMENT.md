# MES+ deployment guide

This is the runbook for deploying the **MES+** AI self-healing layer on
top of the base Industrial RTLS platform. It assumes you have already
followed [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §1 to get the base stack
running.

MES+ is **additive** — it never changes existing services and is gated
behind docker-compose profiles. Bring it up only when ready.

## 1. Prerequisites

- Base RTLS stack running (`docker compose up -d` passes healthchecks).
- ≥ 8 GB RAM free on the host. The MES+ stack adds ~3 GB resident
  (Postgres 200 MB, n8n 400 MB, Grafana 250 MB, Loki 300 MB, Prometheus
  500 MB, Promtail 100 MB, Ollama 1.2 GB with one small model loaded).
- ≥ 20 GB free disk for Prometheus TSDB (15-day retention default) +
  Loki (7-day retention) + n8n executions + Postgres.
- A static plant LAN IP if the workflow's HTTP nodes should reach the
  backend by IP. Container-to-container DNS works inside the compose
  network without this.

## 2. Choose your profile combination

| Need | Profile flag |
|---|---|
| Everything | `--profile mes-plus` |
| Just monitoring on top of base | `--profile observability` |
| Just local LLM (offline AI fallback) | `--profile ai-local` |
| Production with nginx + everything | `--profile prod --profile mes-plus` |

The flags compose — order doesn't matter. You can mix.

## 3. Configure secrets

```bash
cp .env.example .env
```

The new MES+ section starts at the line `# MES+ stack (Phase 1)` near
the bottom of `.env.example`. The **required** fields are:

```dotenv
# These three MUST be set before activation
POSTGRES_PASSWORD=$(openssl rand -hex 24)
N8N_ENCRYPTION_KEY=$(openssl rand -hex 32)
GRAFANA_ADMIN_PASSWORD=$(openssl rand -hex 16)

# At least one AI provider
OPENAI_API_KEY=sk-...

# These two enable real recovery + admin auth
ENABLE_REAL_RECOVERY=false      # leave false until you've verified actions.py
ADMIN_TOKEN=$(openssl rand -hex 32)
```

Optional but recommended for plant use:

```dotenv
ANTHROPIC_API_KEY=...                 # Claude Sonnet for remediation
PINECONE_API_KEY=...                  # Vector memory for incident similarity
PERPLEXITY_API_KEY=...                # Real-time research tool
SLACK_BOT_TOKEN=xoxb-...              # Notifications
GMAIL_OAUTH_CLIENT_ID=...             # Daily summary email
GITHUB_PAT=ghp_...                    # Deployment correlation

# Workflow targets
PROMETHEUS_URL=http://prometheus:9090/api/v1/query
MES_API_BASE=http://backend:8000
OPS_LEADERSHIP_EMAIL=plant-leadership@tbdi.example.com
```

## 4. Bring up the stack

```bash
docker compose --profile mes-plus up -d --build

# wait ~60-90s for healthchecks; then verify:
docker compose ps
# All rtls-* containers should be (healthy).
```

If `rtls-postgres` or `rtls-n8n` is unhealthy, check logs:

```bash
docker compose logs --tail=100 postgres
docker compose logs --tail=100 n8n
```

The most common failure on first boot is `POSTGRES_PASSWORD` mismatch
between an existing `postgres_data` volume and the new `.env`. Fix:
`docker compose --profile mes-plus down -v` (destroys data) and bring up
again with the new password. **Don't** do this on a running production
deployment.

## 5. Import the workflow into n8n

Browser → `http://localhost:5678/`

1. **First-time setup** — create the owner account.
2. **Settings → Environment Variables** — verify n8n sees the
   PROMETHEUS_URL / MES_API_BASE / OPS_LEADERSHIP_EMAIL /
   ADMIN_TOKEN env vars (they come from the container's env_file).
3. **Workflows → Import from File** → `ops/n8n/workflow.phase3.json`
   (the cumulative Phase 2+3 file).
4. **Bind credentials** in the n8n UI (one-time):
   - Postgres credential: host=`postgres`, port=`5432`, db=`mes_incidents`, user=`mes`, password=from `.env`. Bind to all `postgres` and `postgresTool` nodes.
   - OpenAI: API key from `.env`. Bind to `OpenAI GPT-5 Model`, both embeddings nodes.
   - Anthropic: API key. Bind to `Claude Sonnet Model`.
   - Pinecone: API key + environment + index. Bind to both vector store nodes.
   - Slack: OAuth2. Bind to 5 alert nodes.
   - Gmail: OAuth2. Bind to `Email Daily Summary`.
   - Perplexity: API key. Bind to `Real-time Incident Research`.
   - **HTTP Header Auth (MES backend)**: header `Authorization`, value `={{ "Bearer " + $env.ADMIN_TOKEN }}`. Bind to the 4 `Execute *Recovery` nodes.
5. **Don't activate yet.** Run one manual execution from the
   `Chaos Engineering Trigger` to verify all credentials resolve.
6. **Activate** when the manual run goes green end-to-end.

## 6. Verify end-to-end

```bash
# 1) Direct backend recovery test (mock-safe)
curl -X POST http://localhost:8000/admin/recovery/camera/restart \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"camera_id":"CAM001","action":"reconnect"}'
# Expect: 200, action_taken="mocked"

# 2) Trigger the chaos webhook (n8n → recovery → backend)
curl -X POST http://localhost:5678/webhook/cb008cf4-9df2-42c4-a4b0-c5895d9072a3 \
  -H "Content-Type: application/json" \
  -d '{"scenario":"rtsp_drop_burst","cameras":["CAM003"]}'

# 3) Inspect the recovery_log
docker compose exec postgres psql -U mes mes_incidents \
  -c "SELECT component, target_id, action, status, occurred_at FROM recovery_log ORDER BY occurred_at DESC LIMIT 5;"

# 4) Open Grafana → Explore → Loki → log query:
#    {service=~"rtls-.*"} |= "recovery"
```

## 7. Promote to real recovery (when ready)

Until `ENABLE_REAL_RECOVERY=true`, the recovery endpoints log + return
200 without taking any action. To enable real restarts:

1. Wire the TODO blocks in `backend/app/recovery/actions.py` (see
   markers — each one is a focused 20-40 line patch into the existing
   manager modules).
2. Add an integration test that asserts idempotency (same restart call
   twice within 30 s = one real restart).
3. Flip `ENABLE_REAL_RECOVERY=true` in `.env`.
4. `docker compose --profile mes-plus restart backend`.
5. Run the chaos test from §6 again — verify `action_taken="executed"`
   in the response (previously `"mocked"`).

## 8. Backups

- **Postgres**: nightly `pg_dump` of `mes_incidents` and `n8n`:
  ```bash
  docker compose exec postgres pg_dump -U mes mes_incidents | gzip > /backup/mes_incidents_$(date +%F).sql.gz
  docker compose exec postgres pg_dump -U mes n8n          | gzip > /backup/n8n_$(date +%F).sql.gz
  ```
- **n8n workflow JSON**: `ops/n8n/workflow.phase3.json` lives in git.
  Any future workflow change should land here as a new
  `workflow.phaseN.json` so rollback is one import.
- **Redis**: ephemeral by design (cache + breaker state). No backup
  needed; state self-heals within the window.
- **Grafana dashboards**: provisioned from `docker/grafana/dashboards/`.
  Anything created in the UI lives in `grafana_data` volume — back up
  via Grafana's "Export → JSON" if you want it in git.

## 9. Updating

```bash
git pull
docker compose --profile mes-plus pull         # pull pinned image updates
docker compose --profile mes-plus up -d --build
```

If `init.sql` changed and you want the new tables: either run the new
DDL by hand, or destroy + recreate the postgres volume (non-prod only).
Production schema migrations should be done with Alembic or by hand —
never auto-destroy a populated volume.

## 10. Tear-down

```bash
# Stop everything; keep data volumes
docker compose --profile mes-plus down

# Stop AND destroy all data volumes (DANGEROUS in prod)
docker compose --profile mes-plus down -v
```

Note: `--profile mes-plus` covers the new services; the base stack
(backend/frontend/mosquitto/redis) requires plain `docker compose
down` to stop. Bring it up the same way.

## 11. Where things live

| What | Where |
|---|---|
| Workflow JSON (import target) | `ops/n8n/workflow.phase3.json` |
| Original workflow snapshot (rollback) | `ops/n8n/workflow.original.json` |
| Per-phase change docs | `ops/n8n/PHASE*-CHANGES.md` |
| AI subworkflow design specs | `ops/n8n/PHASE4B-DESIGN.md` |
| Architecture + diagrams | `ops/ARCHITECTURE.md` |
| Recruiter / demo script | `ops/DEMO.md` |
| Postgres schema | `docker/postgres/init.sql` |
| Prometheus scrape config | `docker/prometheus/prometheus.yml` |
| Grafana datasources | `docker/grafana/provisioning/datasources/datasources.yml` |
| Grafana dashboards (drop JSON here) | `docker/grafana/dashboards/` |
| Loki config | `docker/loki/loki-config.yml` |
| Promtail config | `docker/promtail/promtail-config.yml` |
| Recovery code | `backend/app/recovery/` |
| Recovery settings | `backend/app/config.py` (search `# MES Recovery`) |

## 12. Operational SLOs (targets, not guarantees)

| Metric | Target |
|---|---|
| Workflow execution latency (p95) | < 8 s |
| AI agent latency (p95) | < 6 s |
| Recovery endpoint p99 | < 2 s |
| Mean time to detect (camera drop) | < 60 s (2-min schedule + 30 s code) |
| Mean time to recover (camera drop) | < 30 s after detect |
| Self-healing success rate | > 95 % over 30-day window |
| Postgres growth | < 1 GB / month (with default retention) |
| Loki growth | < 10 GB / month (with 7-day retention) |
| Prometheus growth | < 5 GB / 15-day retention |
