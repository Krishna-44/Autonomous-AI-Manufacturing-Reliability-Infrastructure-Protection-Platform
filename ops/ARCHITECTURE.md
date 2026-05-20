# Architecture — RTLS + MES+ AI self-healing platform

This is the system after Phases 1-5. Original RTLS stack (mosquitto,
backend, frontend, nginx) is unchanged; the **MES+** profile adds the AI
self-healing layer.

## 1. System overview

```mermaid
flowchart LR
  classDef plant     fill:#fef3c7,stroke:#b45309,color:#7c2d12
  classDef core      fill:#dbeafe,stroke:#1d4ed8,color:#1e3a8a
  classDef mesplus   fill:#dcfce7,stroke:#16a34a,color:#14532d
  classDef obs       fill:#fce7f3,stroke:#be185d,color:#831843
  classDef ext       fill:#f3f4f6,stroke:#6b7280,color:#374151,stroke-dasharray:4 3
  classDef ai        fill:#ede9fe,stroke:#7c3aed,color:#4c1d95

  subgraph PLANT[Bawal plant floor]
    BLE[BLE beacons + Minew gateways]:::plant
    CAM[RTSP cameras]:::plant
    PLC[PLCs + MES line]:::plant
  end

  subgraph CORE[Core RTLS stack — unchanged]
    MQTT[mosquitto]:::core
    REDIS[(redis)]:::core
    BACKEND[FastAPI backend<br/>+ recovery endpoints]:::core
    FRONTEND[Next.js dashboard]:::core
    NGINX[nginx prod profile]:::core
  end

  subgraph MESPLUS[MES+ stack — profile: mes-plus]
    N8N[n8n<br/>cloud or self-host]:::mesplus
    PG[(postgres<br/>mes_incidents + n8n)]:::mesplus
    OLLAMA[ollama<br/>local LLM fallback]:::ai
  end

  subgraph OBS[Observability — profile: observability]
    PROM[prometheus]:::obs
    GRAF[grafana]:::obs
    LOKI[loki]:::obs
    PROMT[promtail]:::obs
  end

  subgraph EXT[External APIs]
    OAI[OpenAI]:::ext
    ANTH[Anthropic]:::ext
    PINE[Pinecone]:::ext
    PPLX[Perplexity]:::ext
    SLACK[Slack]:::ext
    GMAIL[Gmail]:::ext
    JIRA[Jira]:::ext
    GH[GitHub]:::ext
    FW[Firewall API]:::ext
  end

  BLE  --> MQTT
  CAM  --> BACKEND
  PLC  --> BACKEND
  MQTT --> BACKEND
  BACKEND <--> REDIS
  BACKEND --> FRONTEND
  NGINX --> BACKEND
  NGINX --> FRONTEND

  BACKEND -- /metrics --> PROM
  BACKEND -- stdout --> PROMT
  N8N     -- /metrics --> PROM
  N8N     -- stdout --> PROMT
  PROMT   --> LOKI
  PROM    --> GRAF
  LOKI    --> GRAF

  N8N <--> PG
  N8N <--> REDIS
  N8N -.HTTP POST<br/>/admin/recovery/*.-> BACKEND
  N8N --> OAI
  N8N --> ANTH
  N8N --> PINE
  N8N --> PPLX
  N8N --> SLACK
  N8N --> GMAIL
  N8N --> JIRA
  N8N --> GH
  N8N --> FW
  N8N -.fallback LLM.-> OLLAMA
```

The arrow from n8n → backend's `/admin/recovery/*` is the **self-healing
loop**: AI detects something → AI decides action → AI calls backend → backend
performs idempotent restart → AI verifies and either declares success or
escalates.

## 2. AI Incident pipeline (main workflow)

```mermaid
flowchart TD
  classDef ingest fill:#fef3c7,stroke:#b45309
  classDef ai     fill:#ede9fe,stroke:#7c3aed
  classDef store  fill:#dcfce7,stroke:#16a34a
  classDef notify fill:#fce7f3,stroke:#be185d
  classDef route  fill:#dbeafe,stroke:#1d4ed8

  ING[Metrics Ingestion Webhook<br/>or schedule-triggered probe]:::ingest
  NORM[Normalize Metrics Data]:::ingest
  TRIAGE[Incident Triage Agent<br/>gpt-4o + Pinecone retriever<br/>+ Perplexity tool<br/>+ Postgres history tool]:::ai
  ROUTE{Route by Severity}:::route
  REMED[Auto-Remediation Agent<br/>Claude Sonnet]:::ai
  INVEST[Deep Investigation Agent<br/>longer-running]:::ai
  STORE[(incidents table)]:::store
  CACHE[(Redis active-incident cache)]:::store
  VEC[(Redis + Pinecone<br/>vector memory)]:::store
  SCRIT[Alert Slack — Critical]:::notify
  SHIGH[Alert Slack — High]:::notify
  DISC[Discord legacy]:::notify
  LOG[Log Medium/Low Postgres]:::store

  ING --> NORM --> TRIAGE --> ROUTE
  ROUTE -- critical --> REMED --> STORE
  REMED --> CACHE
  REMED --> VEC
  REMED --> SCRIT
  ROUTE -- high --> INVEST --> STORE
  INVEST --> SHIGH
  ROUTE -- medium/low --> LOG
  LOG --> DISC
```

## 3. Self-healing flow (camera example — applies to iframe / ffmpeg / process)

```mermaid
sequenceDiagram
  participant SCH as Camera Health Monitor<br/>(schedule, 2 min)
  participant CHK as Check Camera Streams<br/>(JS code)
  participant AI as Camera Recovery Agent
  participant CB as Circuit Breaker<br/>(redis)
  participant API as Backend<br/>/admin/recovery/camera/restart
  participant CAM as CameraManager
  participant VAL as Validate Iframe Recovery
  participant ESC as Escalate Unrecoverable Camera

  SCH->>CHK: tick
  CHK->>CHK: identify failed streams<br/>(fps=0 OR last_frame > 60s)
  alt all healthy
    CHK-->>SCH: 0 items (workflow noop)
  else degraded streams
    CHK->>AI: failed_cameras[]
    AI->>AI: plan recovery (model-driven)
    AI->>CB: pre-check breaker
    alt breaker open
      CB-->>AI: 503 Retry-After=60
      AI->>ESC: escalate immediately
    else breaker closed/half_open
      CB-->>AI: allow
      AI->>API: POST {camera_id, action, recovery_steps}
      Note over API: idempotency dedup<br/>+ circuit-breaker check
      API->>CAM: recover_camera(id, action)
      CAM-->>API: ok
      API-->>AI: {ok:true, action_taken:"executed"}
      AI->>VAL: validate
      alt healthy now
        VAL-->>SCH: done (recovery succeeded)
      else still failing
        VAL->>ESC: escalate
        ESC->>ESC: alert engineer + create ticket
      end
    end
  end
```

## 4. Data flow (what writes where)

```mermaid
flowchart LR
  classDef src fill:#fef3c7,stroke:#b45309
  classDef tbl fill:#dcfce7,stroke:#16a34a

  TR[Triage Agent]:::src         --> INC[(incidents)]:::tbl
  REM[Remediation Agent]:::src   --> INC
  REM                            --> ACT[(Redis active_incident:*)]:::tbl
  ERR[Error Recovery Trigger]:::src --> WE[(workflow_errors)]:::tbl
  CR[Camera/Iframe/Ffmpeg Recovery]:::src --> RL[(recovery_log)]:::tbl
  PB[Predictive Breakdown AI]:::src --> MT[(maintenance_tickets)]:::tbl
  GH[GitHub Deployment Monitor]:::src --> DEP[(deployments)]:::tbl
  VHM[Video Health Monitor]:::src --> VHM_T[(video_health_metrics)]:::tbl
  CAP[CAPA Agent — phase 4b]:::src --> CAPS[(capas)]:::tbl
  SHIFT[Shift Intelligence — phase 4b]:::src --> SR[(shift_reports)]:::tbl
  CORR[Root-Cause Correlator — phase 4b]:::src --> CO[(correlations)]:::tbl
  VI[Video Inspection — phase 4b]:::src --> VINS[(video_inspections)]:::tbl
```

## 5. Credential / env-var matrix

| Component | Credential type | Required for | Env var (when self-hosting) |
|---|---|---|---|
| n8n core | encryption key | n8n itself | `N8N_ENCRYPTION_KEY` |
| Postgres (mes_incidents) | username/password | every postgres node | `POSTGRES_PASSWORD` |
| OpenAI | API key | 14 agents + 3 embeddings | `OPENAI_API_KEY` |
| Anthropic | API key | Claude Sonnet model | `ANTHROPIC_API_KEY` |
| Pinecone | API key + index | 2 vector store nodes | `PINECONE_API_KEY`, `PINECONE_INDEX` |
| Perplexity | API key | Real-time Incident Research tool | `PERPLEXITY_API_KEY` |
| Slack | Bot token (`xoxb-`) | 5 alert nodes | `SLACK_BOT_TOKEN` |
| Gmail | OAuth2 | Daily Summary email | `GMAIL_*` |
| Jira | API token + base URL | Security ticket creation | `JIRA_*` |
| GitHub | PAT (read) | Deployment Monitor | `GITHUB_PAT` |
| Discord | Webhook URL | Legacy fallback channel | `DISCORD_WEBHOOK_URL` |
| MES backend | HTTP Header Auth | 4 Execute-*Recovery nodes | `ADMIN_TOKEN` |
| Firewall (optional) | _(none — URL only)_ | Block Malicious IPs node | `FIREWALL_BLOCK_URL` |
| Dashboard webhooks (optional) | _(none — URL only)_ | 2 push nodes | `DASHBOARD_WEBHOOK_URL`, `VIDEO_DASHBOARD_WEBHOOK_URL` |

**Graceful degradation:** rows marked "(optional)" can stay blank. The
corresponding workflow node has `onError: continueRegularOutput` (Phase
2) so the workflow keeps running. AI agent nodes whose model credential
is missing **will halt** that branch — they're not optional.

## 6. Network ports (all configurable in `.env`)

| Service | Default | Env var | Profile |
|---|---|---|---|
| backend | 8000 | `API_PORT` | default |
| frontend | 3000 | `FRONTEND_PORT` | default |
| mosquitto | 1883 | `MQTT_PORT` | default |
| redis | 6379 | `REDIS_PORT` | default |
| nginx | 80/443 | `HTTP_PORT`/`HTTPS_PORT` | prod |
| postgres | 5432 (loopback) | `POSTGRES_PORT` | mes-plus |
| n8n | 5678 | `N8N_PORT` | mes-plus |
| prometheus | 9090 | `PROMETHEUS_PORT` | mes-plus / observability |
| grafana | 3001 | `GRAFANA_PORT` | mes-plus / observability |
| loki | 3100 | `LOKI_PORT` | mes-plus / observability |
| ollama | 11434 | `OLLAMA_PORT` | mes-plus / ai-local |

## 7. Failure-domain isolation

The platform is intentionally compartmentalized so a failure in one
domain can't take down others:

- **AI provider outage** (OpenAI / Anthropic down) → only AI agent
  branches halt; ingestion + dashboard + recovery endpoints keep working.
- **n8n down** → MES dashboard + ingestion keep working; no AI triage
  + no self-healing until n8n is back. Manual recovery via dashboard
  buttons still hits the same `/admin/recovery/*` endpoints.
- **Redis down** → idempotency + circuit breaker fall back to in-memory
  per-process. Self-healing degrades to "best effort" (no cross-worker
  dedup) but still works.
- **Postgres down** (mes_incidents) → workflow writes fail, retries 3×
  then routes to the `Error Recovery Trigger` chain. Backend RTLS keeps
  using SQLite as it always did.
- **Backend down** → Camera/Iframe/Ffmpeg recovery POSTs fail their
  retries → workflow escalates via Slack + Discord. Detection still
  fires.
