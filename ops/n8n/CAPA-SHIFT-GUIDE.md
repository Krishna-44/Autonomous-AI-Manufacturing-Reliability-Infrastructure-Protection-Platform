# CAPA Generation + Shift Intelligence Report — import & wire-up

Two standalone subworkflows built from the Phase-4b designs.

| File | Workflow name | Nodes | Trigger | Purpose |
|---|---|---|---|---|
| `workflow.capa.json` | CAPA Generation | 9 | Webhook | Generate Corrective + Preventive Action records from incidents |
| `workflow.shift.json` | Shift Intelligence Report | 12 | Schedule (7/15/23 IST) | AI-written end-of-shift summary for plant leadership |

Both reuse the **same 12 credentials** you already created for `workflow.free.json` — no new credentials needed. Both write to Postgres tables created by `docker/postgres/init.sql` (Phase 4b).

---

## 1. CAPA Generation Workflow

### Flow

```
[Webhook] → [Fetch Incident (Postgres)] → [Build Context]
        → [CAPA AI Agent]   ⤴ Groq Model (llama-3.3-70b)
        ↓                   ⤵ CAPA Schema (structured output)
        → [Persist CAPA (capas table)]
        → [Notify Slack #maintenance] → [Email maintenance team]
```

### Import + wire-up

1. n8n → Workflows → ⋯ → **Import from File** → pick `workflow.capa.json`
2. Bind credentials when prompted — every node already has the right type stub. Click each node and pick from dropdown:
   - 2 × `MES Postgres` (Fetch Incident, Persist CAPA)
   - 1 × `MES Groq` (Groq Model for CAPA)
   - 1 × `MES Slack Bot` (Notify Maintenance Slack)
   - 1 × `MES Gmail` (Email Maintenance)
3. **Save**, then **Activate**.

### Calling it from the main workflow

The CAPA webhook URL after import:
```
https://YOUR-N8N-HOST/webhook/<capa-webhook-uuid>
```
(grab the exact path from the `CAPA Webhook` node — n8n shows it in the node panel).

Add a single `HTTP Request` node to the main workflow right after `Store Incident Record` posting:
```json
{ "incident_id": "{{ $('Store Incident Record').item.json.id }}" }
```
Use the same Phase-3 retry/timeout pattern as the other recovery HTTPs.

### Calling it manually (for testing or dashboard buttons)

```bash
curl -X POST https://YOUR-N8N-HOST/webhook/<capa-webhook-uuid> \
  -H "Content-Type: application/json" \
  -d '{"incident_id": 4231}'
```

### What gets written

A row in the `capas` Postgres table:

| Column | Source |
|---|---|
| incident_id | from the request body |
| title | AI: `summary_title` |
| root_cause / corrective_action / preventive_action | AI |
| owner | AI: `maintenance` / `software` / `supervisor` / `admin` |
| due_at | AI: ISO-8601 deadline |
| severity | AI |
| ai_confidence | AI: 0..1 |
| raw_agent_output | full agent output (JSONB) for audit |

Plus a Slack message to `$SLACK_CHANNEL_MAINTENANCE` and an email to `$ESCALATE_MAINTENANCE`.

---

## 2. Shift Intelligence Report Workflow

### Flow

```
[Cron 7/15/23 IST] → [Compute Shift Window (JS)]
                  → [Q: Incidents] ⤵
                  → [Q: Recovery Log] → [Merge] → [Shift Intelligence Agent] ⤴ Groq Model
                  → [Q: Video Inspections] ⤴                                  ⤵ Shift Schema
                                                                                ↓
                                                                  [Persist Shift Report]
                                                                  ↓                ↓
                                                  [Email Leadership] [Slack Supervisor]
```

### Import + wire-up

1. n8n → Workflows → ⋯ → **Import from File** → pick `workflow.shift.json`
2. Bind credentials (same pool as CAPA — no new ones):
   - 4 × `MES Postgres` (3 queries + 1 persist)
   - 1 × `MES Groq`
   - 1 × `MES Gmail`
   - 1 × `MES Slack Bot`
3. **Save**, then **Activate**.

### Schedule

Cron expression: `0 7,15,23 * * *` interpreted in `Asia/Kolkata` (set in workflow.settings.timezone). Fires at:
- **07:00 IST** — end of night shift A (23:00 → 07:00)
- **15:00 IST** — end of morning shift B (07:00 → 15:00)
- **23:00 IST** — end of afternoon shift C (15:00 → 23:00)

Each run scans the previous 8-hour window.

### What gets written

A row in `shift_reports` per shift:

| Column | Source |
|---|---|
| shift_id | `YYYY-MM-DD-A/B/C` from Compute Shift Window |
| shift_start / shift_end | ISO-8601 boundaries |
| incidents_count, auto_remediated, escalated, ng_count, ct_drift_alerts | AI: `kpis.*` |
| top_issues | AI: `[{issue, count, trend}, ...]` (JSONB) |
| ai_summary | AI: one-paragraph narrative |
| ai_recommendations | AI: `[{priority, action}, ...]` (JSONB) |
| raw_agent_output | full agent output |

Plus an email to `$OPS_LEADERSHIP_EMAIL` and a Slack post to `$SLACK_CHANNEL_HIGH`.

### Manual test (don't wait for the schedule)

n8n editor → open `Shift Intelligence Report` → click the `Shift End Schedule` node → **Execute Node**. The workflow runs once with the current time as the shift-end.

---

## 3. What didn't get built (still spec-only in PHASE4B-DESIGN.md)

| Feature | Status |
|---|---|
| AI Root-Cause Correlator | designed, not built |
| AI Video Inspection (placeholder) | designed, not built |
| AI Cycle Time Drift Detection | designed (overlaps with Predictive Breakdown), not built |
| NG Pattern Intelligence | designed, not built |
| Spare Parts Forecasting | not designed yet |

These can be built using the same pattern as CAPA + Shift (~30-60 min each).

---

## File map after this update

| File | Use |
|---|---|
| `workflow.original.json` | Pre-hardening snapshot (rollback) |
| `workflow.phase3.json` | Bare hardened workflow (no credentials) |
| `workflow.publish-ready.json` | Phase-3 + paid-API credential stubs |
| `workflow.free.json` | Phase-3 + Groq + Ollama + Pinecone re-enabled |
| `workflow.capa.json` | **New** — CAPA Generation subworkflow |
| `workflow.shift.json` | **New** — Shift Intelligence Report subworkflow |
