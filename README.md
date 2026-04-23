# Conversion Engine

An automated B2B outreach and conversion system for **Tenacious Consulting and Outsourcing**. The engine enriches prospect companies with real-time signals, classifies them against an ICP framework, runs a multi-channel nurture sequence (email-first, SMS after warm reply), and uses a LangGraph AI agent to handle replies, write CRM records, and book discovery calls — all behind a kill switch that routes every outbound message to a staff sink until explicitly enabled.

---

## Architecture

```
Inbound webhook (Resend / Africa's Talking / Cal.com)
        │
        ▼
ConversationAgent (LangGraph)
   ├── enrich       → Signal Pipeline (Crunchbase, Playwright, layoffs.fyi, leadership)
   ├── classify     → ICP Classifier (S1–S4 or unqualified) + Bench Match
   ├── check_command → STOP / HELP / UNSUB detection
   ├── llm          → OpenRouter LLM with 9 HubSpot tool schemas
   ├── tools        → HubSpot MCP tool execution
   ├── kill_switch  → Route to staff sink or real prospect
   ├── send_email   → Resend (email) or Africa's Talking (SMS)
   └── persist      → HubSpot upsert + activity log + brief write + booking write
```

### Module Overview

| Directory | Responsibility |
|-----------|----------------|
| `agent/` | LangGraph conversation graph, HubSpot tool schemas, SMS/email dispatch |
| `booking_agent/` | Cal.com slot query and booking with timezone handling and retry |
| `config/` | Shared models, kill switch, env validation, budget guard |
| `crm_writer/` | HubSpot REST client with rate limiting and exponential backoff |
| `icp_classifier/` | Stateless rule-based ICP segment assignment and bench match |
| `nurture_sequencer/` | Prospect FSM, channel hierarchy, message composition |
| `signal_pipeline/` | 7 enrichment sub-components + assembler + standalone FastAPI service |
| `webhooks/` | FastAPI app with email, SMS, Cal.com, and HubSpot webhook endpoints |
| `observability/` | Langfuse tracing, latency recording, cost invoicing |
| `prompts/` | Versioned prompt template text files |
| `scripts/` | One-off demo and integration test scripts |
| `tests/` | 425-test suite (pytest + Hypothesis property-based tests) |

---

## Signal Pipeline

Each prospect company is enriched by four sources in parallel:

| Source | Implementation | Output |
|--------|----------------|--------|
| Crunchbase ODM | Local CSV fuzzy lookup | Firmographics, funding rounds, leadership hires |
| Job posts | Playwright async scraper (BuiltIn, Wellfound, careers pages) | Post count, tech signals |
| layoffs.fyi | Local CSV scan, 120-day window | Layoff event, headcount, percentage |
| Leadership change | Regex over Crunchbase hire records, 90-day window | CTO/VP Eng appointment |

All outputs are merged into a `HiringSignalBrief` (Pydantic v2, `schema_version: "1.0"`) with per-signal confidence scores (`high` / `medium` / `low`). A `CompetitorGapBrief` is assembled separately. Schema validation failure blocks all HubSpot and Langfuse writes.

---

## ICP Segments

| Segment | Criteria |
|---------|----------|
| S1 — Active growth | Series A/B within 180 days AND job_post_count ≥ 5 |
| S2 — Restructuring | Layoff event within 120 days (backfill signal) |
| S3 — AI adopter | AI maturity score ≥ 2 (medium confidence or above) |
| S4 — AI leader | AI maturity score = 3 AND named AI/ML leadership hire |
| Unqualified | None of the above, or confidence below abstention threshold |

---

## Channel Hierarchy

SMS is a **warm-lead-only channel**. The FSM enforces:

1. First outbound is always email
2. SMS is only unlocked after at least one inbound email reply
3. Cold outreach never touches SMS

---

## Kill Switch

All outbound traffic defaults to the **staff sink** (your own email/phone):

```bash
KILL_SWITCH=          # unset → all outbound goes to STAFF_SINK_EMAIL / STAFF_SINK_PHONE
KILL_SWITCH=enabled   # live routing to real prospects
```

CRM writes are not affected by the kill switch.

---

## Prerequisites

- Python 3.12
- [`uv`](https://docs.astral.sh/uv/) package manager
- Playwright browsers (for job post scraping)

---

## Setup

```bash
git clone <repo>
cd Conversion-Engine

# Install dependencies
uv sync --all-extras --dev

# Install Playwright browsers
uv run playwright install chromium

# Copy and populate environment variables
cp .env.example .env
# Edit .env — see Environment Variables section below
```

---

## Environment Variables

### Required at startup

| Variable | Description |
|----------|-------------|
| `OPENROUTER_API_KEY` | LLM gateway API key |
| `LANGFUSE_SECRET_KEY` | Langfuse observability secret key |
| `LANGFUSE_PUBLIC_KEY` | Langfuse observability public key |
| `HUBSPOT_ACCESS_TOKEN` | HubSpot private-app token (`pat-...`) |
| `CAL_API_KEY` | Cal.com API key |
| `RESEND_API_KEY` | Resend email API key |
| `AT_API_KEY` | Africa's Talking SMS API key |

### Optional with defaults

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENROUTER_MODEL` | `qwen/qwen3-235b-a22b` | LLM model identifier |
| `LANGFUSE_BASE_URL` | `https://cloud.langfuse.com` | Langfuse instance URL |
| `CAL_BASE_URL` | `https://api.cal.com` | Cal.com API base URL |
| `CAL_EVENT_TYPE_ID` | `268206` | Cal.com event type to book |
| `AT_USERNAME` | `sandbox` | Africa's Talking account username |
| `AT_SANDBOX` | `true` | `true` = AT simulator; `false` = production |
| `KILL_SWITCH` | _(empty)_ | Empty = staff sink; `enabled` = live routing |
| `STAFF_SINK_EMAIL` | _(none)_ | Email destination when kill switch is off |
| `STAFF_SINK_PHONE` | _(none)_ | SMS destination when kill switch is off |
| `WEBHOOK_BASE_URL` | `https://conversion-engine-l27z.onrender.com` | Public webhook base URL |
| `RESEND_FROM` | `onboarding@resend.dev` | Sender address for outbound email |
| `LLM_BUDGET_USD` | `20.0` | Hard cap on cumulative LLM spend (USD) |
| `HUBSPOT_OWNER_ID` | _(none)_ | HubSpot owner ID for created records |

### Data source paths

| Variable | Example |
|----------|---------|
| `CRUNCHBASE_ODM_PATH` | `/path/to/Crunchbase-dataset-samples` |
| `LAYOFFS_CSV_PATH` | `/path/to/layoffs.fyi` |

---

## Running

### Webhook server

```bash
uvicorn webhooks.handler:app --host 0.0.0.0 --port 8000
```

Webhook endpoints:

| Path | Provider | Events handled |
|------|----------|----------------|
| `POST /webhooks/email` | Resend | `email.replied`, `email.bounced`, `email.delivery_failed` |
| `POST /webhooks/sms` | Africa's Talking | Inbound SMS reply (form POST) |
| `POST /webhooks/cal` | Cal.com | `BOOKING_CREATED` |
| `POST /webhooks/hubspot` | HubSpot | Timeline events |
| `GET  /health` | — | Liveness probe |

Register webhooks in each provider's dashboard:
- Resend → `$WEBHOOK_BASE_URL/webhooks/email`
- Africa's Talking → `$WEBHOOK_BASE_URL/webhooks/sms`
- Cal.com → `$WEBHOOK_BASE_URL/webhooks/cal`

### Signal Pipeline API (standalone)

```bash
uvicorn signal_pipeline.app:app --port 8001
```

```
GET  /health
POST /enrich   {"company_name": "Acme Corp", "company_id": "acme-001"}
```

### Demo scripts

```bash
# End-to-end demo (enrich → classify → compose → HubSpot)
uv run python -m scripts.run_demo --email you@example.com

# Test outbound email via Resend
uv run python -m scripts.test_resend_demo

# Test outbound SMS via Africa's Talking sandbox
uv run python -m scripts.test_sms --phone +251960039108

# Test Cal.com slot availability and booking
uv run python -m scripts.test_cal_demo

# Test HubSpot contact creation via MCP
uv run python scripts/test_hubspot_contact.py

# Test full LangGraph agent graph
uv run python scripts/test_agent_graph.py
```

---

## Testing

```bash
uv run pytest          # run all tests
uv run pytest -v       # verbose output
uv run pytest tests/test_gap_fixes.py -v   # run a specific test file
```

The suite is 425 tests covering all modules with standard unit tests and Hypothesis property-based tests.

### Key invariants verified by Hypothesis

- `OutboundMessage.draft` is always `True`
- SMS channel never fires before at least one email reply
- "Aggressive hiring" language only when `job_post_count ≥ 5` AND `job_post_velocity_60d ≥ 3.0`
- Low-confidence signals produce interrogative phrasing, not assertions
- Competitor gaps are only surfaced when gap confidence is `medium` or `high`
- No staffing capacity commitment when `bench_mismatch = True`

---

## External Services

| Service | Used for | Key config |
|---------|----------|------------|
| **Resend** | Outbound email, inbound reply webhook, bounce events | `RESEND_API_KEY`, `RESEND_FROM` |
| **Africa's Talking** | Outbound SMS, inbound SMS webhook | `AT_API_KEY`, `AT_USERNAME`, `AT_SANDBOX` |
| **HubSpot** | Contact records, activity log, enrichment notes, booking records | `HUBSPOT_ACCESS_TOKEN`, `HUBSPOT_OWNER_ID` |
| **Cal.com** | Slot availability, booking creation | `CAL_API_KEY`, `CAL_BASE_URL`, `CAL_EVENT_TYPE_ID` |
| **OpenRouter** | LLM gateway (default: Qwen3 235B) | `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` |
| **Langfuse** | LLM call tracing, enrichment traces, CRM failure recovery | `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` |
| **Crunchbase ODM** | Firmographics, funding, leadership (local CSV, no API) | `CRUNCHBASE_ODM_PATH` |
| **layoffs.fyi** | Layoff event scan (local CSV) | `LAYOFFS_CSV_PATH` |
| **Playwright** | Job post scraping (no login logic, no captcha bypass) | `playwright install chromium` |

---

## Deployment

The webhook server is deployed on **Render (free tier)**:

1. Set all required environment variables in the Render dashboard
2. Set start command: `uvicorn webhooks.handler:app --host 0.0.0.0 --port $PORT`
3. Note the assigned URL and set `WEBHOOK_BASE_URL`
4. Register the webhook URL with Resend, Africa's Talking, and Cal.com
5. Run `uv run python scripts/setup_hubspot_properties.py` once to create custom HubSpot contact properties (`icp_segment`, `last_enriched_at`, `ai_maturity_score`, `bench_mismatch`)

CI runs `uv run pytest` on every pull request via GitHub Actions.
