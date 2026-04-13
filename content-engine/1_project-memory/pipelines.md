# Pipeline Architecture

All pipelines live in `backend/app/pipelines/`.  
Base class: `BasePipeline` (`pipelines/base.py`) — holds `job_id`, `db`, `job`.  
They are always called from a **Celery task** (sync context), never from FastAPI directly.

---

## Workflow 1: Keyword → Article

**Class:** `KeywordPipeline` in `pipelines/keyword_pipeline.py`  
**Celery task:** `app.worker.tasks.run_keyword_to_article`  
**Trigger:** `POST /jobs` with `workflow_type: keyword_to_article`

### Stage sequence

| Stage | Method | Progress | Description |
|---|---|---|---|
| 1 | `_search()` | 5→20% | DuckDuckGo → top-5 competitor URLs |
| 2 | `_scrape_outline()` | 22→35% | async httpx + BeautifulSoup → H1/H2/H3 + first `<p>` per heading (~500-900 chars/article) |
| 3 | `_analyze_gaps()` | 37→50% | Gemini: gap analysis JSON from outlines |
| 4 | `_generate_outline()` | 52→65% | Gemini: structured article outline (5-10 sections) |
| 5 | `_write_article()` | 67→85% | Gemini per-section HTML — one API call per section |
| 6 | `_generate_images()` | 87→90% | Imagen: featured (16:9) + N content images |
| 7 | `_finalize()` | 91→92% | bleach sanitise → upsert Article → (no auto-publish) |

### Key details
- **Outline extraction** (`_scrape_outline`): uses `_extract_outline_summary()` — headings + first paragraph only, NOT full body. This is the primary token-saving strategy in the current codebase.
- **Image distribution**: `_compute_image_section_indices()` — spreads `content_image_count` images evenly across middle sections (avoids intro/conclusion).
- **IMAGE_PLACEHOLDER_{id}** tags are injected into section writing prompts and substituted with real base64 `<img>` tags in `_finalize()`.
- **Internal linking**: `related_articles` from `job.meta` are injected into section writer prompts.
- **Min scrape threshold**: Requires `>= 2` valid outline results or raises `RuntimeError`.

### Input validation (at `job.meta` construction in API layer)
```python
content_image_count: int  # clamped 0-5
include_featured_image: bool
related_articles: list[{title, url}]  # max 3
```

---

## Workflow 2: Reference URL → Article

**Class:** `ReferencePipeline` in `pipelines/reference_pipeline.py`  
**Celery task:** `app.worker.tasks.run_reference_to_article` *(also `process_link_workflow` legacy)*  
**Trigger:** `POST /jobs` with `workflow_type: reference_to_article`

### Stage sequence

| Stage | Method | Progress | Description |
|---|---|---|---|
| 1+2 | `_fetch()` | 10→30% | `extract_article_content(url)` → clean Markdown via trafilatura |
| 3 | `_rewrite()` | 35→60% | Gemini: SEO rewrite of Markdown → `{title, html_content}` |
| 4 | `_finalize()` | 70→90% | bleach sanitise → upsert Article |

### Key details
- Much simpler than Workflow 1 — no search, no gap analysis, no images.
- `asyncio.run()` used to bridge async scraper into sync Celery context.
- Article lands in `draft_ready` — no images attached.

---

## Shared patterns across both pipelines

### Article upsert (idempotent)
```python
article = db.query(Article).filter(Article.job_id == job.id).first()
if article is None:
    article = Article(job_id=..., user_id=..., integration_id=...)
    db.add(article)
# then set fields and commit
```
Safe for Celery retries — won't duplicate articles.

### Status updates
```python
self.job.status = "searching"   # granular string
self.job.progress_pct = 20
self.db.commit()
```
These are polled by `GET /jobs/{id}` and streamed via SSE.

### Publish flow (NOT in pipeline)
Publishing to WordPress is **not** done inside pipelines.  
Triggered separately by `POST /articles/{id}/publish`.  
Scheduled publishing handled by `publish_scheduled_articles` Celery Beat task.

---

## AI Module: `app/ai/gemini_writer.py`

Key public functions:

| Function | Called by | Description |
|---|---|---|
| `analyze_content_gaps(outlines)` | `KeywordPipeline._analyze_gaps()` | Gap analysis — returns focus_keyword, content_gaps, recommended_angle, etc. |
| `generate_article_outline(gap_analysis)` | `KeywordPipeline._generate_outline()` | Structured outline with `_meta` sentinel + sections list |
| `write_article_from_outline(outline, gap_analysis, ...)` | `KeywordPipeline._write_article()` | Per-section Gemini calls, IMAGE_PLACEHOLDER injection |
| `rewrite_article_for_seo(markdown)` | `ReferencePipeline._rewrite()` | Single-call Markdown → SEO HTML |

**`_call_gemini_json()`** — shared helper with self-correction loop (up to 2 retries on `JSONDecodeError`).

**System prompts defined as module-level constants:**
- `_GAP_ANALYSIS_SYSTEM_PROMPT`
- `_OUTLINE_SYSTEM_PROMPT`
- `_SECTION_WRITER_BASE_PROMPT` (built dynamically via `_build_section_writer_prompt()`)
- `_SYSTEM_PROMPT` (Workflow 2 rewrite)

---

## Adding a New Workflow

1. Create `pipelines/my_pipeline.py` extending `BasePipeline`.
2. Implement `run() -> dict`.
3. Create a task class in `worker/tasks.py` extending `BaseJobTask`, override `execute()`.
4. Register with `celery_app.register_task(...)`.
5. Add `WorkflowType.MY_WORKFLOW` enum value in `models/job.py`.
6. Add to `_TASK_MAP` in `api/v1/jobs.py`.
7. Add schema validation in `schemas/job.py`.
