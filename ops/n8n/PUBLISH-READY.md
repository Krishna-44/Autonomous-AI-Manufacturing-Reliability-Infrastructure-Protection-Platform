# Publish-ready workflow import

`workflow.publish-ready.json` is the same Phase-3 workflow (113 nodes,
all hardening intact) but with **credential stubs pre-attached to all
50 credential-needing nodes**. This collapses the import → publish
flow from ~30 minutes of clicking down to ~5 minutes.

> **What this file is NOT:** it does not contain your real API keys.
> Those have to live in n8n's credential vault (encrypted by
> `N8N_ENCRYPTION_KEY`). What it does is pre-fill every node with the
> *correct credential type* and a descriptive *name slot* so when you
> open n8n's Credentials tab, every node knows exactly what kind of
> key it expects.

## How to use it

### Step 1 — Create 12 credentials in n8n UI (~5 min, one-time)

n8n → **Credentials** → ⊕ New → pick the type, enter your real key,
name it exactly as shown:

| Credential type | Name | Where to put your key |
|---|---|---|
| Postgres | `MES Postgres` | host=`postgres` (or your DB host), port `5432`, db `mes_incidents`, user/pass from `.env` |
| Redis | `MES Redis` | host=`redis`, port `6379` (or your Redis URL) |
| OpenAI | `MES OpenAI` | sk-... |
| Anthropic | `MES Anthropic` | sk-ant-... |
| Pinecone | `MES Pinecone` | API key + index env |
| Perplexity | `MES Perplexity` | pplx-... |
| Slack OAuth2 | `MES Slack Bot` | xoxb-... with chat:write + files:write |
| Gmail OAuth2 | `MES Gmail` | OAuth client + refresh token |
| Jira Software Cloud | `MES Jira` | base URL + email + API token |
| Discord | `MES Discord` | webhook URL or bot token |
| GitHub | `MES GitHub` | PAT with repo:read |
| HTTP Header Auth | `MES Admin Token` | header `Authorization`, value `Bearer YOUR_ADMIN_TOKEN` |

The 12th (HTTP Header Auth) is what the workflow uses to authenticate
into your MES backend's `/admin/recovery/*` routes — see
[`../../backend-patch/`](../../backend-patch/).

### Step 2 — Import the workflow

n8n → Workflows → ⋯ → **Import from File** → pick
`workflow.publish-ready.json`.

You'll see red banners on every credentialed node saying *"Credential
not found"* — that's expected (the placeholder IDs like `MES-POSTGRES`
don't match the UUIDs n8n generated for your real credentials).

### Step 3 — Re-bind in 12 clicks

Click any node showing the red banner. The "Credential" dropdown is
already filtered to the right *type*. Pick your matching credential
(e.g. `MES Postgres` from the dropdown). Click **Save**.

n8n's quick trick: when you re-link a credential on one node, n8n
offers **"Update credentials on similar nodes"** — click yes to bulk-
link all 22 Postgres nodes at once, all 5 Slack nodes at once, etc.

After the 12 unique credentials are linked, every one of the 50
credentialed nodes is bound.

### Step 4 — Publish

Click **Publish** in n8n. The workflow becomes available in MCP
(`availableInMCP: true` is already set), so other agents / dashboards
can call it.

## Why not just embed the API keys?

Embedding keys in JSON would:
- Leak them to anyone who reads the repo
- Bypass n8n's encryption-at-rest (which uses `N8N_ENCRYPTION_KEY`)
- Make key rotation impossible without re-importing

The publish-ready JSON gets you 95% of the way; the last 5 % is
manually pasting your real keys into n8n's encrypted vault. That's
the right place for them.

## What's still on you

The publish-ready JSON does **not** include the 4 Phase-4b
subworkflows (CAPA Generation, Shift Intelligence, Root-Cause
Correlator, Video Inspection) — those are designed but not yet
generated. See [`PHASE4B-DESIGN.md`](PHASE4B-DESIGN.md).

## Diff vs `workflow.phase3.json`

- Same 113 nodes, same 97 connections, same workflow.settings.
- Only change: 50 nodes got a new `credentials` block.
- 0 new placeholders, 0 schedule changes, 0 retry/timeout changes.
