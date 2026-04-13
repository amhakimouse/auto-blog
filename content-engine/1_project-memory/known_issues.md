# Known Issues & Gotchas

Active bugs, workarounds, and things to be aware of when editing this codebase.
Update this file when you discover or fix issues.

---

## Active Issues

### 1. `bleach` allowlist duplicated in 3 places
**Files:** `ai/gemini_writer.py`, `pipelines/keyword_pipeline.py`, `pipelines/reference_pipeline.py`  
**Impact:** Medium — updating the tag allowlist requires 3 edits.  
**Fix:** Consolidate into `utils/html.py` and import from there.

### 2. `sync_wp_links` Celery Beat task has no `integration_id` passed
**File:** `worker/celery_app.py`  
**Impact:** The nightly sync beat task can't actually run for all integrations — needs to be redesigned as a "fan-out" task that queries all active integrations, then enqueues individual `sync_wp_links` calls.  
**Workaround:** Use `POST /integrations/{id}/sync` to trigger per-integration.

### 3. `process_link_workflow` task is legacy
**File:** `worker/tasks.py`  
**Impact:** Two implementations for Workflow 2 (`process_link_workflow` inline + `ReferencePipeline` class).  
**Recommendation:** `run_reference_to_article` (using `_KeywordToArticleTask` pattern) is the canonical one. The inline task is kept for backwards compat but should be removed when safe.

### 4. `asyncio.run()` in `_scrape_outline()` called sequentially
**File:** `pipelines/keyword_pipeline.py`, method `_scrape_outline()`  
**Impact:** URLs are scraped one-at-a-time (sequential `asyncio.run()` calls in a loop). This is safe but slow — could be parallelised with `asyncio.gather()` inside a single `asyncio.run()`.  
**Workaround:** Acceptable for now; Celery task timeout is generous.

### 5. `ArticleStatus` is a plain string class, not an Enum
**File:** `models/article.py`  
**Impact:** No runtime validation when assigning status strings. A typo will silently persist a bad status.  
**Workaround:** Always use the constants (`ArticleStatus.DRAFT_READY`, etc.) — never raw strings.

---

## Performance Notes

- **Gemini section writing is the bottleneck** — 5-10 sections × 1 API call each. Typical job: 90-180 seconds.
- **Imagen image generation** is fast (~5s per image) but adds up with 6 images.
- **DuckDuckGo search** can occasionally return 202/CAPTCHA — `_MIN_SCRAPED` threshold guards against this.

---

## Environment Gotchas

- `DATABASE_URL` **must** start with `postgresql+asyncpg://` — validated at startup.
- GCP credentials file path defaults to `/secrets/gcp-service-account.json` (Docker mount).
- `VERTEX_AI_MODEL` defaults to `gemini-1.5-pro-preview-0514` — update if model is deprecated.
- `SECRET_KEY` must be ≥ 32 characters — pydantic validation will fail otherwise.
