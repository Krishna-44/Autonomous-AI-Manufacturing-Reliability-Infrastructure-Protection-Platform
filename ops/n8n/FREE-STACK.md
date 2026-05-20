# Free-tier import (`workflow.free.json`)

Zero-paid-API variant of the workflow. Uses:

| Capability | Provider | Cost | Why |
|---|---|---|---|
| LLM (triage + remediation) | **Groq** (`llama-3.3-70b-versatile`, `llama-3.1-8b-instant`) | FREE tier — generous rate limits | OpenAI-compatible API, fastest LLM inference on the market |
| Embeddings | **Ollama** (`nomic-embed-text`) via local container | FREE — runs in your `docker-compose.yml` | Already in your stack via the `ai-local` profile |
| Vector memory | **Redis** (workflow's existing `vectorStoreRedis` path) | FREE — already in stack | Redis vector store was always in the workflow; we just stop using the parallel Pinecone path |
| Postgres / Slack / Gmail / Jira / Discord / GitHub | Same | FREE | (all already free tiers) |

## What was removed / disabled (vs the publish-ready import)

| Node | Why disabled |
|---|---|
| `Real-time Incident Research` (Perplexity tool) | Perplexity API requires a paid plan. The Triage Agent loses real-time web research, but keeps Postgres history + Redis chat memory + the LLM's pretrained knowledge. |
| `Historical Incident Retriever` (Pinecone) | Pinecone has a free tier but requires account signup. Workflow's Redis vector store covers the same use case. |
| `Store in Pinecone Vector DB` | Same. |
| `Pinecone Document Loader` | Sub-node of the above; disabled together. |

## What was changed

| Node | Before | After |
|---|---|---|
| `OpenAI GPT-5 Model` | OpenAI gpt-4o | Groq `llama-3.3-70b-versatile` (via `baseURL` override) |
| `Claude Sonnet Model` | Anthropic claude-sonnet-4 | Groq `llama-3.1-8b-instant` (fast tier; node type swapped from `lmChatAnthropic` to `lmChatOpenAi`) |
| `OpenAI Embeddings` | OpenAI `text-embedding-3-small` | Ollama `nomic-embed-text` (via `baseURL` to local Ollama) |
| `Pinecone Embeddings` | OpenAI embeddings | Same as above — also points at Ollama |

Everything else is unchanged: same 113 nodes (4 disabled), same connection graph, same Phase 2 + 3 hardening (env vars, schedules, retry/timeout, IST timezone), same Phase 4a credential stub pattern.

## How to import

### Step 1 — Sign up for Groq (~30 sec, no card)

1. Open https://console.groq.com/keys
2. Sign in with Google/GitHub
3. Create API key → copy it

### Step 2 — Start your stack with Ollama enabled

```bash
docker compose --profile ai-local up -d
# wait ~30s for ollama health
docker compose exec ollama ollama pull nomic-embed-text
# downloads ~275 MB; only needed once
```

### Step 3 — Create 10 credentials in n8n UI

n8n → **Credentials** → ⊕ New (one per row):

| Type | Name | What to enter |
|---|---|---|
| Postgres | `MES Postgres` | host=`postgres`, port `5432`, db `mes_incidents`, user/pass from `.env` |
| Redis | `MES Redis` | host=`redis`, port `6379` |
| OpenAI | `MES Groq` | Paste your Groq API key |
| OpenAI | `MES Ollama (dummy key)` | Any string (e.g. `ollama-noauth`). The Ollama API doesn't validate keys; this just satisfies n8n's schema. |
| Slack OAuth2 | `MES Slack Bot` | Bot token `xoxb-...` |
| Gmail OAuth2 | `MES Gmail` | OAuth client + refresh token |
| Jira Cloud | `MES Jira` | base URL + email + API token |
| Discord | `MES Discord` | Webhook URL or bot token |
| GitHub | `MES GitHub` | PAT |
| HTTP Header Auth | `MES Admin Token` | Header `Authorization`, value `Bearer YOUR_ADMIN_TOKEN` |

### Step 4 — Import `workflow.free.json`

n8n → Workflows → ⋯ → **Import from File** → pick `workflow.free.json`.

You'll see red banners on ~46 nodes ("Credential not found"). Click any node → pick the right credential from the dropdown → n8n's *"Update credentials on similar nodes"* prompt bulk-links the rest.

After 10 unique credentials are linked, **click Publish**.

## Operational notes

- **Groq rate limits (free tier, as of 2026):** ~30 requests/minute for 70B, 200 RPM for 8B. The workflow's busiest path is the Triage Agent which fires once per incident — well within limits.
- **Ollama performance:** `nomic-embed-text` is fast (~50ms on CPU per query). If you have GPU, uncomment the `deploy.resources.reservations.devices` block in `docker-compose.yml` for 5-10× speedup on chat models if you decide to add Ollama LLM later.
- **Latency comparison:** Groq llama-3.3-70b averages ~400-600ms first-token vs OpenAI gpt-4o at ~1-2s. Your workflow will be *faster* than the paid version.
- **Quality tradeoff:** llama-3.3-70b ≈ gpt-4o-mini for structured-output tasks. The triage classification schema works fine. For remediation, `llama-3.1-8b-instant` is "good enough" for a routine remediation plan; bump it to `llama-3.3-70b-versatile` if you see quality dips.

## Upgrade path back to paid (if you want to)

Each disabled feature has a free-tier option that involves signing up but no payment:
- **Pinecone**: free starter index (1M vectors). Just create `MES Pinecone` credential and unset `disabled: true` on the Pinecone nodes.
- **Perplexity**: $5 free credit on signup (one-time). Or use **Tavily** (1K searches/month free) — just add a generic HTTP Request tool to the Triage Agent pointing at `https://api.tavily.com/search`.

## What files exist now

| File | Use |
|---|---|
| `workflow.original.json` | Pre-hardening snapshot (rollback) |
| `workflow.phase3.json` | Production-hardened, no credentials |
| `workflow.publish-ready.json` | Phase 3 + credential stubs (paid APIs) |
| `workflow.free.json` | **This file** — Phase 3 + credential stubs + paid-API swaps |
