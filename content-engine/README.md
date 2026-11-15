# Content Automation Engine 🚀

> **AI-powered SEO article generation** — Keyword → Article & Competitor URL → Article  
> Fully containerized from day one. No local dependencies required.

---

## Architecture at a Glance

```
┌──────────────────────────────────────────────────────────┐
│                    Docker Network                         │
│                                                          │
│  ┌──────────┐    POST /api/v1/jobs    ┌──────────────┐  │
│  │ Frontend │ ──────────────────────► │  FastAPI     │  │
│  │ React/TS │ ◄──────────────────────  │  Backend     │  │
│  │ :5173    │    202 + job_id         │  :8000       │  │
│  └──────────┘                         └──────┬───────┘  │
│                                              │           │
│                                    send_task │           │
│                                              ▼           │
│  ┌──────────┐    job queue          ┌──────────────┐    │
│  │  Redis   │ ◄──────────────────── │  Celery      │    │
│  │  :6379   │ ──────────────────── ► │  Worker      │    │
│  └──────────┘    result             └──────┬───────┘    │
│                                            │             │
│  ┌──────────────┐                          │ SQL         │
│  │  PostgreSQL  │ ◄────────────────────────┘             │
│  │  :5432       │                                        │
│  └──────────────┘                                        │
└──────────────────────────────────────────────────────────┘
```

## Quick Start

### 1. Prerequisites
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running

### 2. Configure environment
```bash
cp .env.example .env
# Edit .env — at minimum set SECRET_KEY and your GCP credentials
```

### 3. Spin up all services
```bash
docker compose up --build
```

### 4. Run database migrations (first time only)
```bash
docker compose exec backend alembic upgrade head
```

### 5. Verify everything is running
| Service    | URL                               |
|------------|-----------------------------------|
| Frontend   | http://localhost:5173             |
| API Docs   | http://localhost:8000/docs        |
| Health     | http://localhost:8000/health      |

---

## Project Structure

```
content-engine/
├── docker-compose.yml          # Orchestrates all services
├── .env.example                # Environment variable template
│
├── backend/
│   ├── Dockerfile              # Multi-stage Python image (FastAPI + Celery)
│   ├── requirements.txt        # Python dependencies
│   ├── alembic.ini             # Alembic migration config
│   ├── alembic/
│   │   ├── env.py              # Async migration runner
│   │   └── versions/           # Migration scripts (auto-generated)
│   │
│   └── app/
│       ├── main.py             # FastAPI app factory + lifespan
│       ├── config.py           # Pydantic-settings (all env vars)
│       ├── database.py         # Async engine, session factory, get_db()
│       ├── logger.py           # Structlog (JSON prod / pretty dev)
│       ├── security.py         # SSRF validation + HTML sanitizer (XSS)
│       │
│       ├── models/             # SQLAlchemy ORM models
│       │   ├── user.py         # User (UUID PK, plan, bcrypt-ready)
│       │   ├── job.py          # Job (status lifecycle, JSONB meta)
│       │   └── article.py      # Article (sanitized HTML, SEO fields)
│       │
│       ├── schemas/            # Pydantic v2 request/response schemas
│       │   ├── job.py          # JobCreate (validated workflow input)
│       │   └── article.py      # ArticleRead
│       │
│       ├── api/
│       │   ├── router.py       # Aggregates all versioned routers
│       │   └── v1/
│       │       ├── jobs.py     # POST /jobs  GET /jobs/{id}  DELETE /jobs/{id}
│       │       └── articles.py # GET /articles/{id}  GET /articles/by-job/{id}
│       │
│       ├── worker/
│       │   ├── celery_app.py   # Celery factory (broker, queues, reliability)
│       │   └── tasks.py        # Task shells with dead-letter error handling
│       │
│       ├── pipelines/          # Strategy Pattern workflow implementations
│       │   ├── base.py         # Abstract BasePipeline
│       │   ├── keyword_pipeline.py    # Workflow 1 (stub)
│       │   └── reference_pipeline.py # Workflow 2 (stub)
│       │
│       ├── scrapers/           # Isolated, swappable scraper modules
│       │   └── (duckduckgo.py, content.py — to implement)
│       │
│       └── ai/                 # AI client abstractions
│           └── (vertex_client.py, prompts.py — to implement)
│
└── frontend/
    ├── Dockerfile              # Node.js + Vite dev server
    ├── package.json            # React 18, TypeScript, TanStack Query
    └── vite.config.ts          # Proxy /api → backend, bind 0.0.0.0
```

---

## Common Docker Commands

```bash
# Start all services
docker compose up -d

# View logs for a specific service
docker compose logs -f backend
docker compose logs -f worker

# Create a new Alembic migration
docker compose exec backend alembic revision --autogenerate -m "add_column_x"

# Apply migrations
docker compose exec backend alembic upgrade head

# Open a Python shell in the backend container
docker compose exec backend python

# Inspect Celery — list active tasks
docker compose exec worker celery -A app.worker.celery_app inspect active

# Stop all services
docker compose down

# Full reset (removes volumes/data!)
docker compose down -v
```

---

## Pipeline Implementation (Workflows)

The "brain" of the engine is built using the **Strategy Pattern**: each workflow
is an isolated `BasePipeline` subclass. The Celery task shell handles all retry
and status-lifecycle concerns; pipelines contain only domain logic.

### Workflow 2 — Competitor Link → Article (`ReferencePipeline`)

```
POST /api/v1/jobs  {url}
        │
        ▼   [FastAPI]  SSRF-validate URL → create Job → dispatch task
        │
        ▼   [Celery Worker]
┌──────────────────────────────────────────────────────────────┐
│  ReferencePipeline.run()                                     │
│                                                              │
│  _fetch()  ──asyncio.run()──►  extract_article_content(url) │
│            (httpx + trafilatura → Markdown)                  │
│                                                              │
│  _rewrite(markdown)  ──────►  rewrite_article_for_seo()     │
│                               (Gemini → JSON {title, html}) │
│                                                              │
│  _finalize(payload)  ──────►  bleach.clean()                │
│                          ──►  Article upsert in PostgreSQL   │
└──────────────────────────────────────────────────────────────┘
```

**Status lifecycle:** `pending → scraping → writing → completed / failed`

---

### Workflow 1 — Keyword → Article (`KeywordPipeline`)

```
POST /api/v1/jobs  {keyword}
        │
        ▼   [FastAPI]  validate → create Job → dispatch task
        │
        ▼   [Celery Worker]
┌──────────────────────────────────────────────────────────────┐
│  KeywordPipeline.run()                                       │
│                                                              │
│  _search()      ──────►  DuckDuckGoScraper.search()         │
│                          (httpx POST → BeautifulSoup parse)  │
│                          → list[url]                         │
│                                                              │
│  _scrape(urls)  ──────►  extract_article_content() × N      │
│                          (asyncio.run() per URL, best-effort)│
│                          → list[{url, content_markdown}]     │
│                                                              │
│  _analyze_gaps() ─────►  analyze_content_gaps()  [Gemini]   │
│                          → {focus_keyword, content_gaps, …}  │
│                                                              │
│  _generate_outline() ─►  generate_article_outline() [Gemini]│
│                          → list[{heading, key_points, …}]   │
│                                                              │
│  _write_article() ────►  write_article_from_outline()       │
│                          (Gemini × N sections → HTML)        │
│                                                              │
│  _finalize() ─────────►  bleach.clean()                     │
│                     ──►  Article upsert (title, html, SEO…) │
└──────────────────────────────────────────────────────────────┘
```

**Status lifecycle:** `pending → searching → scraping → analyzing → outlining → writing → completed / failed`

---

### File Map (implemented modules)

| File | Responsibility |
|------|---------------|
| `app/scrapers/article_scraper.py` | Async httpx fetch + trafilatura extraction → Markdown |
| `app/scrapers/duckduckgo.py` | Sync DuckDuckGo HTML scraper → ranked URL list |
| `app/ai/gemini_writer.py` | All Gemini calls: rewrite, gap analysis, outline, section writing |
| `app/pipelines/reference_pipeline.py` | Workflow 2 orchestration (4 stages) |
| `app/pipelines/keyword_pipeline.py` | Workflow 1 orchestration (7 stages) |
| `app/worker/tasks.py` | Celery task shells — retry logic, DB sessions, status lifecycle |

---

### Gemini Call Strategy

| Function | Purpose | Output format | Temp |
|---|---|---|---|
| `rewrite_article_for_seo()` | One-shot rewrite of scraped article | JSON `{title, html_content}` | 0.7 |
| `analyze_content_gaps()` | Strategic analysis of competitor articles | JSON `{focus_keyword, gaps, …}` | 0.4 |
| `generate_article_outline()` | Structured H2/H3 section plan | JSON `{title, sections:[…]}` | 0.5 |
| `write_article_from_outline()` | Per-section HTML generation (1 call/section) | Raw HTML string | 0.75 |

All JSON-returning calls use `response_mime_type="application/json"` for
deterministic parsing. Section writing uses a separate model call per section
to stay within per-request token limits for long articles.

---

## Common Docker Commands

```bash
# Start all services
docker compose up -d

# View logs for a specific service
docker compose logs -f backend
docker compose logs -f worker

# Create a new Alembic migration
docker compose exec backend alembic revision --autogenerate -m "add_column_x"

# Apply migrations
docker compose exec backend alembic upgrade head

# Open a Python shell in the backend container
docker compose exec backend python

# Inspect Celery — list active tasks
docker compose exec worker celery -A app.worker.celery_app inspect active

# Stop all services
docker compose down

# Full reset (removes volumes/data!)
docker compose down -v
```

---

## Key Engineering Decisions

| Decision | Rationale |
|----------|-----------| 
| **Async FastAPI** | API layer never blocks; only validates + dispatches |
| **Celery over ARQ** | Richer ecosystem, proven reliability, retry primitives |
| **`task_acks_late=True`** | Task re-queued if worker crashes mid-execution |
| **Sync SQLAlchemy in worker** | Celery runs in threads/processes, not async event loop |
| **Async SQLAlchemy in API** | FastAPI is fully async; avoids blocking the event loop |
| **SSRF validation before DB write** | Fail fast; don't waste a DB record on invalid input |
| **`asyncio.run()` bridge** | Lets async scraper code run inside sync Celery workers safely |
| **bleach HTML sanitizer** | AI output is untrusted; strip XSS vectors before storage |
| **Strategy Pattern pipelines** | Swap scrapers/AI providers without touching task shell |
| **JSONB `meta` field on Job** | Flexible intermediate data without schema migrations |
| **Per-URL scrape best-effort** | One bad competitor URL won't abort the whole keyword job |
| **`response_mime_type=application/json`** | Constrains Gemini to valid JSON — no regex parsing needed |
| **DuckDuckGo HTML endpoint** | No API key required; plain HTML POST is stable and scriptable |

---

## Implemented RoadMap

- [x] **Infrastructure** — FastAPI, PostgreSQL, Redis, Celery, Alembic (done)
- [x] **Scraper** — `article_scraper.py`: async httpx + trafilatura (done)
- [x] **Search** — `duckduckgo.py`: HTML scraper with rate-limit detection (done)
- [x] **AI: Rewrite** — `rewrite_article_for_seo()`: Gemini one-shot rewrite (done)
- [x] **AI: Gap Analysis** — `analyze_content_gaps()`: multi-article strategy (done)
- [x] **AI: Outline** — `generate_article_outline()`: structured section plan (done)
- [x] **AI: Write** — `write_article_from_outline()`: section-by-section HTML (done)
- [x] **Pipeline** — `ReferencePipeline` fully wired end-to-end (done)
- [x] **Pipeline** — `KeywordPipeline` fully wired end-to-end (done)
- [x] **Auth** — JWT authentication implemented (FastAPI + passlib/bcrypt)
- [x] **Imagen** — Contextual image generation per article section (done)
- [x] **WordPress** — WP REST API publisher via httpx + Application Passwords (done)
- [x] **Frontend** — React components: job form, status poller, article viewer
- [x] **Rate Limiting** — Per-user throttling implemented
- [x] **Monitoring** — Celery Flower service + structured log aggregation
