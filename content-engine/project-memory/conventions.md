# Code Conventions

Patterns and rules established in this codebase. Follow these when adding code.

---

## Imports

- **Inline imports inside methods** for heavy dependencies (Vertex AI, scrapers):
  ```python
  def execute(self, ...):
      from app.pipelines.keyword_pipeline import KeywordPipeline  # ← lazy
      return KeywordPipeline(...).run()
  ```
  Reason: keeps Celery worker startup fast; avoids loading GPU/network SDKs until needed.

- **Module-level imports** for lightweight utilities (structlog, uuid, datetime, etc.)

---

## Logging

Use `structlog` bound loggers with rich context keys:
```python
import structlog
log = structlog.get_logger(__name__)

# Bind context once per scope
logger = log.bind(job_id=job_id, pipeline="keyword", keyword=keyword)

# Log events as snake_case dot-separated namespaced strings
logger.info("keyword_pipeline.search.done", url_count=5)
logger.warning("keyword_pipeline.scrape_outline.url_failed", url=url, error=str(exc))
logger.error("gemini_writer.api_error", error=str(exc))
```

**Never use `print()` or `logging.info()`** — always structlog.

---

## Error Handling

### In pipelines
- Let exceptions bubble up to the Celery task shell (`BaseJobTask.run()`) which catches them, marks job `failed`, and retries.
- Only catch specific exceptions when you want to continue (e.g., per-URL scrape failures):
  ```python
  except ArticleScraperError as exc:
      logger.warning("url_failed", error=str(exc))
      continue  # skip this URL, try next
  ```

### In API layer
- Never catch broad `Exception` — let FastAPI handle 500s.
- Use `raise HTTPException(status_code=..., detail="...")` for expected user errors.

### Custom exceptions
Defined close to where they're raised:
- `GeminiWriterError`, `GeminiResponseParseError`, `GeminiEmptyResponseError` — in `ai/gemini_writer.py`
- `SSRFViolationError` — in `security/`
- `ArticleScraperError`, `ArticleFetchError`, `ArticleExtractionError` — in `scrapers/`
- `WordPressAuthError` — in `integrations/wordpress.py`

---

## Database Access

### In FastAPI routes (async)
```python
from app.database import get_db
from sqlalchemy.ext.asyncio import AsyncSession

async def my_endpoint(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
```

### In Celery tasks/pipelines (sync)
```python
from app.worker.tasks import _SyncSession  # or create locally

with _SyncSession() as db:
    job = db.query(Job).filter(Job.id == uuid.UUID(job_id)).first()
```

**Never use `asyncio.run()` inside FastAPI routes.** Only in Celery tasks.

---

## Pydantic Schemas vs ORM Models

- **Schemas** (`app/schemas/`) — for request/response validation only.
- **Models** (`app/models/`) — for DB ORM only.
- Never import a schema into a model, or vice versa.
- `response_model=JobRead` in router → FastAPI auto-serialises the ORM object.

---

## Settings

Always access via:
```python
from app.config import settings
settings.vertex_ai_model  # do this
```

Never `import os; os.environ.get(...)` in application code.

---

## Naming Conventions

| Thing | Convention | Example |
|---|---|---|
| Files | `snake_case.py` | `keyword_pipeline.py` |
| Classes | `PascalCase` | `KeywordPipeline` |
| Celery tasks (registered) | `snake_case` | `run_keyword_to_article` |
| Celery task names (string) | `app.worker.tasks.<name>` | |
| Log event keys | `module.substage.action` | `keyword_pipeline.search.done` |
| DB columns | `snake_case` | `progress_pct` |
| API routes | `kebab-case` (path) | `/jobs/{job_id}` |
| Env vars | `UPPER_SNAKE_CASE` | `GCP_PROJECT_ID` |

---

## HTML Safety

All HTML that comes from Gemini must be run through `bleach.clean()` before storage:
```python
import bleach
clean_html = bleach.clean(
    dirty_html,
    tags=_ALLOWED_TAGS,
    attributes=_ALLOWED_ATTRIBUTES,
    strip=True,
    strip_comments=True,
)
```
The `_ALLOWED_TAGS` / `_ALLOWED_ATTRIBUTES` allowlists are duplicated in:
- `ai/gemini_writer.py`
- `pipelines/keyword_pipeline.py`
- `pipelines/reference_pipeline.py`

> **TODO:** Consolidate these into a single `utils/html.py` module.

---

## Agent Skills (in `agent_skills/`)

Skills are **pure functions or small classes** — no side effects, no DB access, no HTTP calls
(unless the skill is specifically a fetcher, like `serp_tracker`).

```python
# agent_skills/my_skill.py
def my_skill(input_data: dict) -> dict:
    """
    One-line description of what this skill does.
    
    Args:
        input_data: dict with keys: ...
    Returns:
        dict with keys: ...
    """
    ...
```

Register in `agent_skills/registry.py` and document in `memory/agent_skills_index.md`.
