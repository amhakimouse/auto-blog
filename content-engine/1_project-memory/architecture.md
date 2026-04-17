# Architecture — Content Automation Engine

## System Overview

A SaaS backend that automates SEO article generation and WordPress publishing.
Users submit **Jobs** via REST API → a **Celery worker** processes them asynchronously
→ articles are stored in Postgres and can be published to WordPress.

```
User / Frontend
      │  REST API (FastAPI)
      ▼
  ┌─────────────────────────────┐
  │  FastAPI App (uvicorn)      │
  │  /api/v1/*                  │
  │  JWT auth · rate limiting   │
  └────────────┬────────────────┘
               │ celery.send_task()
               ▼
  ┌─────────────────────────────┐
  │  Redis (broker + result)    │
  └────────────┬────────────────┘
               │
               ▼
  ┌─────────────────────────────┐
  │  Celery Worker(s)           │
  │  ├── keyword_pipeline       │
  │  ├── reference_pipeline     │
  │  └── maintenance tasks      │
  └────────────┬────────────────┘
               │ writes
               ▼
  ┌─────────────────────────────┐
  │  PostgreSQL                 │
  │  users · integrations       │
  │  jobs · articles            │
  └─────────────────────────────┘

  External services called by worker:
  ├── Vertex AI / Gemini  (text generation — via SDK or direct REST API)
  ├── Vertex AI / Imagen  (image generation)
  ├── DuckDuckGo HTML     (competitor search, free)
  ├── httpx + trafilatura (article scraping)
  ├── FlareSolverr        (Cloudflare bypass for scraping)
  └── WordPress REST API  (publish)
```

---

## Technology Choices

| Layer | Choice | Why |
|---|---|---|
| Web framework | FastAPI | async-native, auto-docs, Pydantic v2 |
| Task queue | Celery 5 + Redis | battle-tested, already in stack |
| DB ORM | SQLAlchemy 2 async | typed `Mapped[]`, async session |
| DB driver | asyncpg (API) / psycopg2 (worker) | Celery runs in threads — no async |
| AI text | Vertex AI Gemini 1.5 Pro | best quality, via REST or SDK |
| AI image | Vertex AI Imagen | matches Gemini project |
| HTML safety | bleach | allowlist-based XSS sanitiser |
| Logging | structlog | structured JSON, bound context |
| Auth | JWT (python-jose) + bcrypt | stateless tokens |
| Rate limiting | slowapi | per-user key func |
| Scraping | Trafilatura + FlareSolverr | best-in-class extraction + bypass |

---

## Key Design Principles

1. **API layer = thin shell.** It validates, creates a DB row, sends a Celery task, returns 202.
2. **Worker contains all logic.** Pipeline classes handle scraping, AI calls, DB writes.
3. **Idempotent upserts.** Every pipeline uses get-or-create for Article rows — safe for retries.
4. **Async API, sync worker.** FastAPI uses asyncpg; Celery workers use psycopg2 + `asyncio.run()` to bridge async scrapers.
5. **Secrets via env vars.** Never hardcoded. `app/config.py` (pydantic-settings) is the single source of truth.

---

## Entry Points

| File | Role |
|---|---|
| `backend/app/main.py` | FastAPI app factory, lifespan, CORS, middleware |
| `backend/app/api/router.py` | Aggregates all versioned routers |
| `backend/app/worker/celery_app.py` | Celery app config, beat schedule |
| `backend/app/worker/tasks.py` | All Celery task definitions |
| `backend/app/config.py` | All settings (env-validated via pydantic-settings) |
| `backend/app/database.py` | Async engine + session factory |

---

## Directory Map

```
backend/
└── app/
    ├── ai/              # Gemini + Imagen client wrappers
    ├── api/             # FastAPI routers (auth, jobs, articles, integrations, events)
    ├── integrations/    # WordPress publisher
    ├── models/          # SQLAlchemy ORM models
    ├── pipelines/       # Workflow business logic (keyword + reference)
    ├── scrapers/        # DuckDuckGo + article HTTP scrapers
    ├── schemas/         # Pydantic request/response schemas
    ├── security/        # SSRF validation, JWT auth
    ├── worker/          # Celery app + task definitions
    └── agent_skills/    # ← pluggable skill modules (see agent_skills_index.md)
```
