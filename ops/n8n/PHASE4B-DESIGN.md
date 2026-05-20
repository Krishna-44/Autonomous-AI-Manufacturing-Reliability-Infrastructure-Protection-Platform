# Phase 4b — Four new AI subworkflows (design spec)

Phase 4b adds four AI-powered subworkflows on top of the existing
113-node platform. Each is **independent and importable as a new n8n
workflow** — they don't modify the existing `AI Incident Intelligence &
Self-Healing Platform copy`.

This document is a **build-from-spec guide**, not workflow JSON. Reason:
hand-generating new LangChain-agent JSON from scratch is brittle —
sub-node wiring (`ai_languageModel`, `ai_outputParser`, `ai_memory`
connection types) is easy to break and hard to verify visually. The n8n
UI builds these correctly by clicking. Each spec below is detailed enough
to construct in the UI in 30-60 minutes.

If you want me to generate JSON for any one of them, ask and I'll do it
in a follow-up turn.

## Postgres tables (already provisioned in Phase 4b)

`docker/postgres/init.sql` now creates:

| Table | Used by |
|---|---|
| `capas` | CAPA Generation |
| `shift_reports` | Shift Intelligence |
| `correlations` | Root-Cause Correlator |
| `video_inspections` | Video Inspection Placeholder |

Re-run init by recreating the volume:
`docker compose --profile mes-plus down -v && docker compose --profile mes-plus up -d`
(only in non-prod — destroys data).

---

## 1. CAPA Generation Workflow

**Purpose:** Given an incident, generate a Corrective + Preventive Action
record (root cause, corrective action, preventive action, owner, due date)
and route it to maintenance. The platform's Triage Agent already does
*classification* — CAPA Generation does *what we'll do about it long term*.

**Trigger:** Webhook (called by the main workflow's `Store Incident
Record` postcondition, or manually from the dashboard).

### Nodes (9)

| # | Name | Type | Key parameters |
|---|---|---|---|
| 1 | CAPA Webhook | `n8n-nodes-base.webhook` | path: `capa/generate`, method: POST, response: respond when last node finishes |
| 2 | Fetch Incident | `n8n-nodes-base.postgres` | SELECT * FROM incidents WHERE id = `{{ $json.body.incident_id }}` |
| 3 | Build Context | `n8n-nodes-base.set` | Compose `service`, `severity`, `root_cause`, `metrics_snapshot` into one prompt context object |
| 4 | CAPA AI Agent | `@n8n/n8n-nodes-langchain.agent` | promptType: `define`; text: see prompt below; hasOutputParser: true |
| 5 | Anthropic Model (sub) | `@n8n/n8n-nodes-langchain.lmChatAnthropic` | model: `claude-sonnet-4`; temperature: 0.2 |
| 6 | CAPA Schema (sub) | `@n8n/n8n-nodes-langchain.outputParserStructured` | schemaType: fromJson; see schema below |
| 7 | Persist CAPA | `n8n-nodes-base.postgres` | INSERT INTO capas with fields from agent output |
| 8 | Notify Slack | `n8n-nodes-base.slack` | channel: `{{ $env.SLACK_CHANNEL_MAINTENANCE }}`, blocks format |
| 9 | Email Maintenance | `n8n-nodes-base.gmail` | sendTo: `{{ $env.ESCALATE_MAINTENANCE }}` |

### Connections
```
CAPA Webhook → Fetch Incident → Build Context → CAPA AI Agent → Persist CAPA → Notify Slack → Email Maintenance
                                                       ↑
                                          (sub-node connections:)
                              Anthropic Model ──ai_languageModel─▶ CAPA AI Agent
                              CAPA Schema     ──ai_outputParser──▶ CAPA AI Agent
```

### CAPA Agent prompt (paste verbatim)
```
You are a senior reliability engineer at Toyota Boshoku Device India's
Bawal MES plant. An incident has occurred and an automated triage has
classified it. Your job is to produce a CAPA (Corrective + Preventive
Action) record.

Input incident:
service: {{ $json.service }}
severity: {{ $json.severity }}
category: {{ $json.category }}
probable_root_cause: {{ $json.root_cause }}
recommended_action: {{ $json.recommended_action }}
metrics_snapshot: {{ $json.metrics_snapshot }}

Produce:
- root_cause:        one sentence, plain English, factual
- corrective_action: what fixes THIS incident now (1-3 steps)
- preventive_action: what prevents recurrence in the next 30 days (1-3 steps)
- owner:             role responsible — one of: maintenance | software | supervisor | admin
- due_at:            ISO-8601 deadline (be realistic: 24-72 h for critical, 1 week for high, 2 weeks otherwise)
- severity:          critical | high | medium | low
- ai_confidence:     0..1 how confident you are this CAPA is correct
- summary_title:     short title for dashboards (<= 60 chars)

Be specific. "Add monitoring" is not specific. "Add Prometheus alert
when fusion queue depth > 500 for 60s" is specific.
```

### CAPA output schema (paste into Structured Parser as `jsonSchemaExample`)
```json
{
  "root_cause": "RTSP keepalive timed out due to network congestion on PoE switch port 12.",
  "corrective_action": ["Replace PoE cable on port 12", "Reseat camera CAM003"],
  "preventive_action": ["Add Prometheus alert on port-12 link flap", "Schedule quarterly cable test in MaintApp"],
  "owner": "maintenance",
  "due_at": "2026-05-23T18:00:00+05:30",
  "severity": "high",
  "ai_confidence": 0.82,
  "summary_title": "Camera CAM003 RTSP drop — PoE port flap"
}
```

### Postgres insert (Persist CAPA node)
Table: `capas` | Mode: `defineBelow`
| Column | Value (n8n expression) |
|---|---|
| incident_id | `={{ $('Fetch Incident').item.json.id }}` |
| title | `={{ $('CAPA AI Agent').item.json.output.summary_title }}` |
| root_cause | `={{ $('CAPA AI Agent').item.json.output.root_cause }}` |
| corrective_action | `={{ JSON.stringify($('CAPA AI Agent').item.json.output.corrective_action) }}` |
| preventive_action | `={{ JSON.stringify($('CAPA AI Agent').item.json.output.preventive_action) }}` |
| owner | `={{ $('CAPA AI Agent').item.json.output.owner }}` |
| due_at | `={{ $('CAPA AI Agent').item.json.output.due_at }}` |
| severity | `={{ $('CAPA AI Agent').item.json.output.severity }}` |
| ai_confidence | `={{ $('CAPA AI Agent').item.json.output.ai_confidence }}` |
| raw_agent_output | `={{ $('CAPA AI Agent').item.json.output }}` |

### Credentials needed
- Postgres (mes_incidents DB)
- Anthropic API
- Slack (Bot Token, scopes `chat:write`, `files:write`)
- Gmail OAuth2

### How to integrate
After importing as a new workflow, copy its webhook URL and add an
`HTTP Request` node in the main workflow right after `Store Incident
Record` that POSTs `{ "incident_id": "{{ $json.id }}" }` to this URL.
Use the same retry+timeout pattern as Phase 3.

---

## 2. Shift Intelligence Report Workflow

**Purpose:** At end of each shift, generate an AI-written summary of
incidents + production + recovery activity + operator notes. Email goes
to plant leadership; Slack post to the supervisor channel.

**Trigger:** Schedule, 3× per day at IST shift boundaries.

### Nodes (10)

| # | Name | Type | Key parameters |
|---|---|---|---|
| 1 | Shift End Schedule | `n8n-nodes-base.scheduleTrigger` | cron: `0 7,15,23 * * *`, tz Asia/Kolkata |
| 2 | Compute Shift Window | `n8n-nodes-base.code` | JS that derives shift_id, shift_start (8 h ago), shift_end (now); maps hour→shift label A/B/C |
| 3 | Fetch Incidents | `n8n-nodes-base.postgres` | SELECT severity, category, COUNT(*), MAX(detected_at) FROM incidents WHERE detected_at BETWEEN $shift_start AND $shift_end GROUP BY severity, category |
| 4 | Fetch Recovery Log | `n8n-nodes-base.postgres` | SELECT component, status, COUNT(*) FROM recovery_log WHERE occurred_at BETWEEN ... GROUP BY component, status |
| 5 | Fetch Video Inspections | `n8n-nodes-base.postgres` | SELECT verdict, COUNT(*) FROM video_inspections WHERE inspected_at BETWEEN ... GROUP BY verdict |
| 6 | Merge Shift Data | `n8n-nodes-base.merge` | mode: combineByPosition (3 inputs) |
| 7 | Shift Intelligence Agent | `@n8n/n8n-nodes-langchain.agent` | hasOutputParser: true |
| 8 | OpenAI Model (sub) | `@n8n/n8n-nodes-langchain.lmChatOpenAi` | model: `gpt-4o`, temperature: 0.3 |
| 9 | Shift Schema (sub) | `@n8n/n8n-nodes-langchain.outputParserStructured` | see schema below |
| 10 | Persist + Email + Slack | `n8n-nodes-base.postgres` then `gmail` then `slack` | three sequential nodes |

### Compute Shift Window code (paste into Code node)
```javascript
const now = new Date();
const hr  = now.getHours();   // IST since workflow.timezone is set
// shifts: A 23-07, B 07-15, C 15-23 (IST). Trigger fires at end of shift.
let shiftLabel, shiftEndHour;
if (hr === 7)  { shiftLabel = 'A'; shiftEndHour = 7;  }
else if (hr === 15) { shiftLabel = 'B'; shiftEndHour = 15; }
else { shiftLabel = 'C'; shiftEndHour = 23; }
const shiftEnd = new Date(now); shiftEnd.setMinutes(0,0,0);
const shiftStart = new Date(shiftEnd.getTime() - 8 * 60 * 60 * 1000);
const date = shiftEnd.toISOString().slice(0, 10);
return [{ json: {
  shift_id:    `${date}-${shiftLabel}`,
  shift_label: shiftLabel,
  shift_start: shiftStart.toISOString(),
  shift_end:   shiftEnd.toISOString(),
}}];
```

### Shift output schema
```json
{
  "ai_summary": "Shift B (15:00-23:00 IST) saw 3 incidents (1 critical, 2 medium). The critical was a camera RTSP drop on CAM005 auto-recovered in 42s. NG rate was 1.2% (target <= 2%). Recovery success 100%.",
  "top_issues": [
    {"issue": "Camera RTSP drops", "count": 2, "trend": "up"},
    {"issue": "Fusion queue depth spikes", "count": 1, "trend": "flat"}
  ],
  "ai_recommendations": [
    {"priority": "high", "action": "Investigate CAM005 PoE link quality (2nd drop this week)"},
    {"priority": "medium", "action": "Tune fusion queue alert threshold from 500 to 400"}
  ],
  "kpis": {"incidents": 3, "auto_remediated": 3, "escalated": 0, "ng_count": 12, "ct_drift_alerts": 0}
}
```

### Postgres insert (Persist node)
Table: `shift_reports` | Map agent output fields to columns (shift_id,
shift_start, shift_end from Compute node; other fields from agent).

### Email recipients
`={{ $env.OPS_LEADERSHIP_EMAIL }}`

### Slack channel
`{{ $env.SLACK_CHANNEL_HIGH }}` (or add a new SLACK_CHANNEL_SHIFT env var)

---

## 3. AI Root-Cause Correlator Workflow

**Purpose:** Every 15 min (and on-demand), look across PLC / RTSP / DB /
server-load / NG / deployments signals from the last window. Ask the AI
to identify patterns ("a deploy at 14:32 preceded 5 fusion errors at
14:34") and persist correlations.

**Trigger:** Schedule (`*/15 * * * *`) + Webhook `/correlate/now`.

### Nodes (13)

| # | Name | Type | Key parameters |
|---|---|---|---|
| 1 | Schedule (15 min) | `scheduleTrigger` | minutesInterval: 15 |
| 2 | Webhook (on-demand) | `webhook` | path: `correlate/now`, POST |
| 3 | Compute Window | `code` | window_start = now - 15min; window_end = now |
| 4 | Q: Incidents | `postgres` | SELECT id, service, severity, category, detected_at FROM incidents WHERE detected_at BETWEEN ... |
| 5 | Q: Deployments | `postgres` | SELECT * FROM deployments WHERE deployed_at BETWEEN ... |
| 6 | Q: Workflow Errors | `postgres` | SELECT * FROM workflow_errors WHERE occurred_at BETWEEN ... |
| 7 | Q: Recovery Failures | `postgres` | SELECT * FROM recovery_log WHERE status = 'failed' AND occurred_at BETWEEN ... |
| 8 | Q: Video Fails | `postgres` | SELECT camera_id, COUNT(*) FROM video_inspections WHERE verdict = 'fail' AND inspected_at BETWEEN ... GROUP BY camera_id |
| 9 | Merge Signals | `merge` | mode: append, 5 inputs |
| 10 | Root-Cause Agent | `agent` | hasOutputParser: true |
| 11 | Model + Schema (sub-nodes) | `lmChatAnthropic` + `outputParserStructured` | see schema |
| 12 | Confidence Gate | `if` | condition: `{{ $json.output.confidence }}` > 0.7 |
| 13 | Persist + Alert | `postgres` (always) + `slack` (high-confidence branch) | INSERT into correlations always; Slack alert only when gate passes |

### Connection diagram
```
Schedule ─┐
          ├─▶ Compute Window ─┬─▶ Q: Incidents ──┐
Webhook ──┘                   ├─▶ Q: Deployments ┤
                              ├─▶ Q: Errors    ──┤
                              ├─▶ Q: Recovery  ──┼─▶ Merge ─▶ Root-Cause Agent ─▶ Confidence Gate ─┬─▶ Persist (always)
                              └─▶ Q: Video     ──┘                                                 └─▶ Slack (only if conf > 0.7)
```

### Correlator agent prompt
```
You are correlating signals from a manufacturing plant in a 15-minute
window ({{ $('Compute Window').item.json.window_start }} →
{{ $('Compute Window').item.json.window_end }}).

You are given parallel slices of:
- incidents       : {{ JSON.stringify($('Q: Incidents').all().map(x => x.json)) }}
- deployments     : {{ JSON.stringify($('Q: Deployments').all().map(x => x.json)) }}
- workflow_errors : {{ JSON.stringify($('Q: Workflow Errors').all().map(x => x.json)) }}
- recovery_log    : {{ JSON.stringify($('Q: Recovery Failures').all().map(x => x.json)) }}
- video_fails     : {{ JSON.stringify($('Q: Video Fails').all().map(x => x.json)) }}

Identify the strongest causal pattern (if any). Produce a confidence
score; if there's no strong pattern, return confidence < 0.5 and
pattern_label = "no significant correlation".

Examples of strong patterns:
- "PLC scan delay > 2× normal within 60s of a deploy that touched plc_*"
- "Camera RTSP drops on N>2 cameras within 30s of each other" (network event)
- "NG spike + CT drift on the same line within 5 min" (mechanical issue)
```

### Correlator output schema
```json
{
  "pattern_label": "Camera RTSP drops cluster",
  "confidence": 0.86,
  "primary_incident_id": 4231,
  "sources_involved": ["incidents", "recovery_log"],
  "signal_count": 4,
  "evidence": [
    {"source": "incidents", "signal_id": "4231", "weight": 1.0, "summary": "CAM003 rtsp_drop @ 14:32:11"},
    {"source": "incidents", "signal_id": "4232", "weight": 0.9, "summary": "CAM005 rtsp_drop @ 14:32:18"},
    {"source": "recovery_log", "signal_id": "9912", "weight": 0.7, "summary": "ffmpeg restart on CAM003 failed"}
  ],
  "suggested_action": "Check PoE switch SW-FLOOR-2; multiple cameras dropping within 30s suggests upstream network event."
}
```

---

## 4. AI Video Inspection Pipeline (placeholder)

**Purpose:** Today the workflow has *health* checks for cameras (is the
stream up?). This subworkflow adds *content* checks (was the production
cycle's video frame OK?). Implementation is a mock scorer; a real CV
model swaps in later without schema changes.

**Trigger:** Webhook called by the RTSP pipeline once per production
cycle (per camera).

### Nodes (7)

| # | Name | Type | Key parameters |
|---|---|---|---|
| 1 | Inspection Webhook | `webhook` | path: `video-inspect`, POST, responds with verdict |
| 2 | Validate Input | `set` | Extract camera_id, cycle_id, frame_url; reject if missing |
| 3 | Score Frame | `code` | Mock CV: returns `{verdict, score, model:'mock-v0', findings:[]}`. See code below. Future: replace with HTTP node calling a real CV service gated by `VIDEO_CV_URL` env var. |
| 4 | Persist Inspection | `postgres` | INSERT INTO video_inspections (...) |
| 5 | Verdict Router | `switch` | cases: pass / fail / uncertain |
| 6 | Alert on Fail | `slack` | channel: SLACK_CHANNEL_CAMERA, only on `fail` branch |
| 7 | Respond | `respondToWebhook` | return `{ ok: true, verdict, score, model }` |

### Mock scorer code
```javascript
// Deterministic mock — same input always returns same verdict.
// Replace with an HTTP call to the future CV service.
const { camera_id, cycle_id } = $input.first().json;
const hash = (camera_id + cycle_id).split('').reduce((a, c) => a + c.charCodeAt(0), 0);
const score = ((hash % 100) / 100);
let verdict = 'pass';
if (score < 0.2) verdict = 'fail';
else if (score < 0.4) verdict = 'uncertain';
return [{ json: {
  camera_id, cycle_id,
  frame_url: $input.first().json.frame_url,
  verdict, score, model: 'mock-v0',
  findings: verdict === 'fail' ? [{ class: 'missing_part', confidence: 1 - score }] : []
}}];
```

### Calling this from the RTSP pipeline (in backend)
Add one line at the end of each frame-completion handler:
```python
# backend/app/camera/rtsp_ingest.py — pseudo
import httpx
await httpx.AsyncClient().post(
    f"{settings.n8n_webhook_base}/webhook/video-inspect",
    json={"camera_id": cam_id, "cycle_id": cycle_id, "frame_url": frame_url},
    timeout=2,
)
```
Or skip the per-cycle call and run a periodic batch via the existing
`Camera Health Monitor` schedule.

---

## Importing these subworkflows

For each:
1. n8n → ⊕ → Create Workflow → name it (e.g. `CAPA Generation`)
2. Add nodes in the order above; n8n auto-connects sequential ones
3. Bind credentials when prompted
4. Set workflow settings (Workflow Settings → timezone IST,
   executionTimeout 300, save error executions = all)
5. Save → activate

**Or** ask me to generate the JSON for any one — I'll do it in a
follow-up so each gets focused attention.

## Cross-cutting requirements

- All four subworkflows require the **same Postgres credential** (DB:
  `mes_incidents`) as the main workflow.
- AI agents need either **OpenAI** or **Anthropic** credentials (or
  both — mix per workflow).
- Slack notifications need one **Slack OAuth2** credential with
  `chat:write` scope, invited to the relevant channels.
- Schedule triggers use the workflow-level timezone (`Asia/Kolkata`),
  which Phase 3 set on the main workflow. Re-apply per new workflow in
  Settings.
