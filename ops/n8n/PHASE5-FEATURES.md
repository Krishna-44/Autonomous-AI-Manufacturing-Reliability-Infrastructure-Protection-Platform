# Phase 5 — Advanced AI features (20-feature roadmap)

This document covers the **20 advanced automation ideas** layered on top of
the existing platform. Each is categorized by the realistic effort to ship
it, and 4 of them are already built as importable workflow JSONs.

> **Important:** Nothing in Phase 5 touches the existing workflows. All
> changes are **additive** — new workflows, new Postgres tables, new docs.
> The core 113-node workflow + CAPA + Shift Intelligence are untouched.

## Status overview

| Tier | What it is | Items | What's delivered |
|---|---|---|---|
| **A — Built now** | New importable workflow JSONs, same pattern as CAPA / Shift | 4 features | 4 workflow JSONs in this folder |
| **B — Already in the platform** | Existing functionality, just need polish | 4 features | (already shipped in earlier phases) |
| **C — Backend / infra extensions** | FastAPI patches + new endpoints | 3 features | designed below |
| **D — Major standalone systems** | Multi-week projects with significant new infra | 9 features | architecture sketches below |

---

## Tier A — Built and importable now

### A1. `workflow.why-failed.json` — AI "Why Machine Failed" Explainer

**Trigger:** Webhook `POST /webhook/<uuid>/why-failed` with body `{incident_id}`.

**Flow:** Fetch incident + 15-min context window from Postgres → Groq agent
writes plain-language failure narrative → persist to `failure_explanations`
table → respond to caller with the narrative.

**Output shape:**
```json
{
  "narrative": "Machine YNC-L6 failed because hydraulic pressure sagged 14% in 90 s before the CT alert; operator was idle...",
  "factors": [{"factor": "hydraulic pressure drop", "weight": 0.7, "evidence": "..."}],
  "confidence": 0.83,
  "suggested_action": "Replace hydraulic accumulator on YNC-L6 within 48h"
}
```

**Wire it from the main workflow:** After `Store Incident Record`, add a
single `HTTP Request` node POSTing `{ "incident_id": ... }` to this
workflow's webhook URL. Failure narratives go straight into Slack alerts
via a downstream merge.

**Credentials:** 2× `MES Postgres`, 1× `MES Groq` (already in your pool).

**9 nodes** — Webhook → 2 parallel Postgres queries → Merge → AI Agent (+
Groq Model sub + Why-Failed Schema sub) → Persist → Respond.

---

### A2. `workflow.bottleneck.json` — AI Bottleneck Detector

**Trigger:** Schedule every 30 min (IST).

**Flow:** Query 3 parallel views of the last 60 min (station cycle times,
recovery action pressure, camera NG rate) → AI agent identifies the single
biggest bottleneck + projects 2-hour loss → persist to `bottleneck_analyses`
→ if confidence ≥ 0.6 send Slack alert.

**Output shape:**
```json
{
  "slowest_station": "ST-04",
  "bottleneck_factor": "hydraulic_lag",
  "impact_summary": "ST-04 averaged 28s CT vs 20s target; hydraulic pressure logs show 14% sag at peak load",
  "projected_loss_parts": 370,
  "projected_loss_currency": 18500,
  "confidence": 0.78,
  "recommendations": [
    {"priority": "high", "action": "Run hydraulic pressure diagnostic on ST-04"}
  ]
}
```

**Requires:** `production_cycles` table populated by your MES line collector
(schema added to `init.sql` by Phase 5). Until that's wired, the workflow
runs but with empty results.

**Credentials:** 4× `MES Postgres`, 1× `MES Groq`, 1× `MES Slack Bot`.

**12 nodes** — Schedule → Compute Window → 3 parallel Postgres queries →
Merge → AI Agent → Persist → If gate → Slack alert.

---

### A3. `workflow.knowledge-engine.json` — AI Knowledge Engine (RAG)

**Trigger:** Webhook `POST /webhook/<uuid>/knowledge` with body
`{question, user_id?, top_k?}`.

**Flow:** Embed the question via Ollama → semantic search in Pinecone for
similar past incidents/CAPAs → Groq agent synthesizes the answer using only
retrieved context → persist Q&A to `knowledge_qa` → respond with
`{answer, sources, confidence}`.

**Why it matters:** This is the "Factory ChatGPT" idea from feature #6 in
a focused, scoped form. Operators ask plain-English questions, the AI
answers from the actual incident history. No hallucinated suggestions —
only from the retrieved context.

**Example query:**
```bash
curl -X POST https://YOUR-N8N/webhook/<uuid> \
  -d '{"question": "Why does CAM003 keep dropping at night?", "top_k": 5}'
# →
# { "answer": "CAM003 has dropped 7 times in the last 30 days, all between
#   22:00-04:00. The pattern matches PoE switch SW-FLOOR-2 thermal
#   throttling identified in incident #4231 (resolved by adding a fan).",
#   "sources": [4231, 4287, 4322],
#   "confidence": 0.86 }
```

**Credentials:** 1× `MES Postgres`, 1× `MES Pinecone`, 1× `MES Ollama (dummy)`, 1× `MES Groq`.

**9 nodes** — Webhook → Extract Query → Pinecone Retrieve (Ollama
Embeddings sub) → AI Agent (Groq sub + Knowledge Schema sub) → Persist Q&A
→ Respond.

---

### A4. `workflow.spare-forecast.json` — AI Spare Part Failure Forecasting

**Trigger:** Schedule daily at 06:00 IST (`cron 0 6 * * *`).

**Flow:** Query 3 historical views (180 days of consumption, 90 days of
breakdowns, 90 days of recovery patterns) → AI agent produces up to 10
forecasts → split into per-part rows → persist each to `spare_forecasts`
AND auto-create a row in `procurement_requests` → notify maintenance Slack.

**Output shape (per forecast):**
```json
{
  "part_id": "HYD-VALVE-21",
  "part_name": "Hydraulic valve assy 21",
  "machine_id": "YNC-L6",
  "predicted_failure_date": "2026-06-04",
  "days_until_failure": 15,
  "confidence": 0.78,
  "reasoning": "Consumed 4x in last 60d; same machine had 3 recovery failures last week",
  "recommended_action": "Order 2 units; schedule replacement during shift C",
  "order_quantity": 2,
  "urgency": "high"
}
```

**Requires:** `spare_parts_consumption` table populated by maintenance team
(schema added to `init.sql`). Pre-populate with last 6 months of data to
get useful forecasts on day 1.

**Credentials:** 3× `MES Postgres`, 1× `MES Groq`, 1× `MES Slack Bot`.

**12 nodes** — Schedule → 3 parallel queries → Merge → AI Agent →
Split Out forecasts → 2 parallel persist nodes (forecasts + procurement)
→ Slack.

---

## Tier B — Already in the platform

These features from your spec are ALREADY shipped in earlier phases. No
new work needed; they may just need polish or wiring to new dashboards.

| # | Feature | Where it lives | Status |
|---|---|---|---|
| 3 | Self-Healing Factory Infrastructure | `workflow.free.json` + `backend-patch/app/recovery/` | ✅ Live. Restart-collector, RTSP repair, ffmpeg kill, log rotation are covered. Add real-impl in `actions.py` TODOs. |
| 9 | AI Incident Replay Engine | Existing `Incident Replay Trigger` webhook + `Fetch Incident Timeline` + `Generate Incident Replay` nodes in the main workflow | ✅ Basic version live. Polish: add a frontend timeline viewer (Tier D). |
| 12 | Auto Failure Escalation Matrix | `Error Recovery Trigger` chain + `Alert Engineer - Camera Failure` + `escalate_*` env vars in `backend-patch/app/config.py` | ✅ Basic version live. Polish: AI-driven role-routing (decides component → role) is a Tier C extension. |
| 19 | AI Collector Health Engine | `Process Watchdog Monitor` (every 30s) + `Check Process Health` + `Process Recovery Agent` | ✅ Live. Polish: auto-tune polling interval is a Tier C extension. |

---

## Tier C — Backend / infrastructure extensions (designed, not built)

Each is a 1-3 file patch into `backend-patch/app/`. The patches follow
the same pattern as `app/recovery/` from Phase 4a.

### C1. Zero-Downtime Deployment AI (#8)

**Where:** `backend-patch/app/deployment/`
- `pre_check.py` — async function that returns a structured snapshot:
  current production load (cycles per minute), DB health, camera stability
  (last 5-min uptime), active collectors, MQTT message rate.
- `routes.py` — `GET /admin/deploy/pre-check`: returns the snapshot + an
  LLM-decided safe-window suggestion.
- `safe_window.py` — Uses Groq via `httpx` to ask: "given this snapshot
  and the next 4 hours' shift schedule, when is the safest 8-minute window
  to deploy?"

**Integration:** GitHub Actions workflow can call this endpoint pre-deploy
and refuse to proceed if `production_load > threshold` or
`safe_window_now == false`.

**Effort:** ~120 lines + a few hours of integration testing.

### C2. Autonomous Camera Load Balancing (#18)

**Where:** `backend-patch/app/camera_balancer/`
- `queue.py` — Redis-backed priority queue. Each ffmpeg extract job has a
  priority (live > on-demand > batch).
- `worker.py` — Pool of N async workers consuming the queue. N is
  auto-scaled based on `os.cpu_count()` and current load.
- Hook from existing `app.camera.rtsp_ingest` to enqueue instead of
  spawning ffmpeg directly.

**Integration:** The workflow's `Execute Ffmpeg Recovery` doesn't change.
The backend's existing camera supervisor now uses the queue.

**Effort:** ~200 lines + careful testing under load.

### C3. AI Safety Monitoring (real CV, #14)

**Where:** `backend-patch/app/safety/`
- Requires a vision model service (ollama vision, or a separate CV
  container with YOLOv8 / `ultralytics`).
- `ppe_detector.py` — detects hard-hat / vest / glove from camera frames.
- `unauthorized_zone.py` — uses existing geofences from RTLS.
- `fatigue_detector.py` — operator-pose analysis (slumped, idle > N).
- Each writes to `video_inspections` table; AI Triage Agent can already
  consume these as incidents.

**Effort:** depends on which CV model. Skeleton + mock detector ~150 lines;
real model integration days to weeks.

---

## Tier D — Major standalone systems (architecture sketches)

These are multi-week projects. Not realistic to build in a single turn —
listing the architecture so they can be planned as their own milestones.

### D1. Digital Twin of Production Line (#1)

**What:** Real-time virtual clone of machines + PLC states + cycle flow +
operator activity, with AI simulating future states (bottlenecks, failures,
throughput impact, maintenance scenarios).

**Architecture sketch:**
- New service `mes-twin` (Python + FastAPI) subscribing to PLC + MES MQTT
  topics.
- In-memory graph (`networkx` or custom) representing machines/stations/
  buffers as nodes, conveyors/handoffs as edges.
- Discrete-event simulator (`SimPy`) running in parallel to real time,
  fed by current state at every tick.
- LLM agent (Groq) called every 5 min to interpret simulation results
  into actionable narrative.
- New tables: `twin_snapshots`, `twin_simulations`, `twin_predictions`.

**Effort:** 2-4 weeks. Best built incrementally — start with a single
station + conveyor twin and expand.

### D2. Factory ChatGPT (#6)

**What:** Conversational interface for operators ("Why is CT increasing?
Show last 5 NG videos. Which machine fails most on night shift?").

**Architecture sketch:**
- The Knowledge Engine workflow (A3, already built) is the LLM backend.
- New frontend chat panel: send query → call Knowledge Engine webhook →
  render answer with citation links.
- For multi-modal queries ("show last 5 NG videos"), extend the agent
  with **Postgres tool** (already in pattern: query `video_inspections`
  WHERE verdict='fail') and **video URL tool** that resolves cycle IDs
  to S3/MinIO video URLs.
- Multi-turn memory: use Redis chat memory (already wired in the main
  workflow).

**Effort:** ~1 week if you already have the dashboard frontend. The
backend (Knowledge Engine) is already done.

### D3. AI Video Search Engine (#7)

**What:** Semantic search over production cycle videos ("show all cycles
where abnormal motion occurred", "NG happened", "CT > 25s",
"missing part detected").

**Architecture sketch:**
- New service `mes-video-index` (Python). For each cycle's frame batch:
  - Generate video embedding (e.g., CLIP or a temporal model like
    VideoMAE).
  - Store the embedding + cycle metadata in Pinecone (separate index
    from `mes-incidents`).
- New workflow `workflow.video-search.json`: webhook → embed query →
  Pinecone search → return cycle metadata + video URLs.
- For structured queries ("CT > 25s"), bypass the embeddings entirely
  and use Postgres `production_cycles` (already in `init.sql`).
- For semantic queries ("abnormal motion"), use the embedding path.

**Effort:** 1-2 weeks. The video-embedding model selection is the hard
choice; rest is plumbing.

### D4. Autonomous Root-Cause Graph (#10)

**What:** Visual AI reasoning graph (RTSP Lag → Missed frame → PLC delay
→ Cycle mismatch → NG spike).

**Architecture sketch:**
- The Triage Agent and Why-Failed Explainer already produce structured
  cause-effect chains in their output JSON.
- New frontend component that renders these as a flowchart using
  `react-flow` or `mermaid.js`.
- The data source is just `failure_explanations.factors` JSONB column
  (already populated by A1).

**Effort:** Pure frontend work — 1-3 days.

### D5. AI Energy Optimization (#11)

**What:** Monitor machine runtime / idle energy / compressor / lighting /
HVAC; recommend shutdown schedules.

**Architecture sketch:**
- **Prerequisite:** energy metering. Either pull from existing power
  meters (Modbus/MQTT) or add `power_meter` collectors.
- New table: `energy_consumption (machine_id, kw, timestamp)`.
- Daily scheduled workflow (like A4 spare forecast pattern) that AI-
  analyzes consumption and produces recommendations.

**Effort:** ~1 week once meter data is flowing. The AI part is small;
the meter integration is the work.

### D6. AI Shift Optimizer (#5)

**What:** AI compares shifts / operators / machine behavior / NG% / CT
trends; recommends staffing adjustments, operator swaps, maintenance
windows.

**Architecture sketch:**
- Extends `workflow.shift.json` (already built). Same pattern:
  - Add 2 more Postgres queries (operator productivity, machine
    behavior per shift).
  - Add a new structured-output field `staffing_recommendations` to the
    Shift Intelligence Schema.
- Or build as `workflow.shift-optimizer.json` — separate workflow that
  runs weekly and produces a comparative report.

**Effort:** ~30-60 min per workflow following A4's pattern.

### D7. AI Production Loss Estimator (#15)

**What:** Real-time calculation of current downtime cost + projected
hourly loss.

**Architecture sketch:**
- Already partially covered by the Bottleneck Detector (A2) which produces
  `projected_loss_currency`. To go realtime:
  - New endpoint `GET /admin/loss/current` in `backend-patch/`. Queries
    last 10 min of `production_cycles` against the target rate, applies
    a `INR_PER_PART` config value.
  - Frontend dashboard widget polls the endpoint every 30 s.

**Effort:** ~80 lines backend + a small frontend widget.

### D8. AI Knowledge Engine — extended (#16)

The basic RAG is built (A3). The "extended" version learns from CAPA
records (already in `capas` table) and maintenance logs. To extend:

- Add a daily indexing job: every CAPA closed → embed → upsert into
  Pinecone with metadata `{type: 'capa', incident_id, owner, severity}`.
- Knowledge Engine then retrieves both incidents AND CAPAs, producing
  richer answers.

**Effort:** ~1 hour for the indexing job (similar pattern to
`Store in Pinecone Vector DB` already in the main workflow).

### D9. Live Factory Command Center (#17)

**What:** Combined real-time dashboard pulling PLC + RTSP + MES + incidents
+ AI predictions + deployments + operators + maintenance + infrastructure
health.

**Architecture sketch:** This is a frontend project. The data layer already
exists:
- Postgres: `incidents`, `recovery_log`, `capas`, `shift_reports`,
  `bottleneck_analyses`, `failure_explanations`, `production_cycles`,
  `video_inspections`.
- WebSocket: existing backend `/ws` channel.
- Grafana: existing dashboards for system metrics.

A Next.js dashboard pulling from these + Grafana embed iframes would be
the build. ~2-4 weeks for a polished version.

### D10. AI Factory Copilot (#20)

**What:** The "elite" composite — AI acts as SRE + maintenance engineer +
quality engineer + supervisor assistant + deployment monitor + incident
investigator.

**Architecture sketch:** This is the **sum** of A1 (Why-Failed) + A2
(Bottleneck) + A3 (Knowledge) + A4 (Spare Forecast) + C1 (Zero-Downtime
Deploy) + the existing main workflow's Triage Agent. Wire them together
behind a single chat UI (D2). Not a new system per se — it's the
narrative wrapper over everything else.

**Effort:** Once D2 is done, ~3 days to plumb the additional tools.

---

## Files added in Phase 5

| File | Type | Status |
|---|---|---|
| `workflow.why-failed.json` | importable | ✅ built |
| `workflow.bottleneck.json` | importable | ✅ built |
| `workflow.knowledge-engine.json` | importable | ✅ built |
| `workflow.spare-forecast.json` | importable | ✅ built |
| `docker/postgres/init.sql` | 7 new tables added | ✅ updated |
| `PHASE5-FEATURES.md` | this doc | ✅ |

## Credentials needed

**Zero new credentials.** All 4 new workflows reuse the existing pool you
already created for `workflow.free.json`:
- MES Postgres
- MES Groq
- MES Pinecone (for A3 only)
- MES Ollama (dummy key) (for A3 only)
- MES Slack Bot
