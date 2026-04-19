# Pipeline Architecture

All pipelines live in `backend/app/pipelines/`.  
Base class: `BasePipeline` (`pipelines/base.py`) — holds `job_id`, `db`, `job`.  
They provide a unified `_set_step()` helper to atomically update `status`, `progress_pct`, and `meta["current_step_label"]`.  
They are always called from a **Celery task** (sync context), never from FastAPI directly.

---

## Workflow 1: Keyword → Article

**Class:** `KeywordPipeline` in `pipelines/keyword_pipeline.py`  
**Celery task:** `app.worker.tasks.run_keyword_to_article`  
**Trigger:** `POST /jobs` with `workflow_type: keyword_to_article`

### 9-Stage Sequence

| Stage | Name | Progress | Description |
|---|---|---|---|
| 1 | `searching` | 5% | DuckDuckGo → top-5 competitor URLs |
| 2 | `scraping` | 15% | Concurrent async scrape of top-3 URLs (`article_scraper.py`) |
| 3 | `content_extracted` | 35% | Scraped content stored; shown in frontend table |
| 4 | `analyzing` | 45% | Single-shot Gemini unified analysis (`run_unified_analysis`) |
| 5 | `awaiting_preview` | 60% | Analysis table shown; pipeline pauses (SSE closes); user approves |
| 6 | `generating_assets` | 65-70% | Concurrent Imagen + WebP + WP upload via `asyncio.gather` |
| 7 | `writing` | 85% | Gemini final HTML structuring with real image URLs injected |
| 8 | `publishing` | 92% | WP create draft post + RankMath SEO meta (`PATCH /wp-json/rank-math-api/v1/update-meta/`) |
| 9 | `completed` | 100% | Article row saved (`save_article`); credits deducted |

### Key Details
- **Concurrent Images:** Replaced Celery `group().get()` with a single `asyncio.run(asyncio.gather(*tasks))` inside the main worker process to fix a concurrency=1 deadlock. Image failures are isolated and never abort the job.
- **Unified Analysis:** Single Gemini call extracts title, slug, SEO fields, summary, and exactly `N` image prompts simultaneously.
- **Frontend Live Tracking:** `job.meta["current_step_label"]` is updated during long stages (like asset generation) to stream granular progress (e.g. "Generating image 2/3...").

---

## Workflow 2: Reference URL → Article

**Class:** `ReferencePipeline` in `pipelines/reference_pipeline.py`  
**Celery task:** `app.worker.tasks.run_reference_to_article`  
**Trigger:** `POST /jobs` with `workflow_type: reference_to_article`

### 8-Stage Sequence

| Stage | Name | Progress | Description |
|---|---|---|---|
| 1 | `scraping` | 10% | Extract content from the reference URL |
| 2 | `content_extracted` | 30% | Extracted content stored; shown in frontend |
| 3 | `analyzing` | 45% | Single-shot Gemini unified analysis |
| 4 | `awaiting_preview` | 60% | Analysis table shown; pipeline pauses; user approves |
| 5 | `generating_assets` | 65-70% | Concurrent Imagen + WebP + WP upload |
| 6 | `writing` | 85% | Gemini final HTML structuring |
| 7 | `publishing` | 92% | WP create draft post + RankMath SEO meta |
| 8 | `completed` | 100% | Article row saved |

### Key Details
- Identical stages 3-8 to KeywordPipeline. It utilizes the exact same shared helpers from `_pipeline_helpers.py`.

---

## Shared Pipeline Helpers (`_pipeline_helpers.py`)

To ensure parity between pipelines, stages 5-9 use shared utility functions:
- `run_concurrent_image_pipeline()`: Concurrent image generation, WebP conversion (Pillow), and WP media upload.
- `generate_final_html()`: Passes the unified analysis and image URLs to Gemini for final HTML structuring; runs `bleach.clean()` on the output.
- `save_article()`: Idempotent upsert of the `Article` row; calculates accurate plain-text `word_count`.

---

## AI Module: `app/ai/gemini_writer.py`

Key public functions:

| Function | Called by | Description |
|---|---|---|
| `run_unified_analysis(source_content, num_images)` | Both Pipelines (Stage 4) | Single-shot analysis: extracts title, slug, SEO fields, summary, and exactly `num_images` Imagen prompts. |

**`_call_gemini_json()`** — shared helper with self-correction loop (up to 2 retries on `JSONDecodeError`).

---

## Adding a New Workflow

1. Create `pipelines/my_pipeline.py` extending `BasePipeline`.
2. Implement `run() -> dict`. Use `self._set_step(...)` extensively.
3. Pause at `JobStatus.AWAITING_PREVIEW` if human-in-the-loop is needed.
4. Create a task class in `worker/tasks.py` extending `BaseJobTask`, override `execute()`.
5. Register with `celery_app.register_task(...)`.
6. Add `WorkflowType.MY_WORKFLOW` enum value in `models/job.py`.
7. Add to `_TASK_MAP` in `api/v1/jobs.py`.
8. Add schema validation in `schemas/job.py`.
