# Workers & Celery Tasks

**File:** `backend/app/worker/tasks.py`  
**Celery app:** `backend/app/worker/celery_app.py`  
**Broker:** Redis at `CELERY_BROKER_URL`  
**Result backend:** Redis at `CELERY_RESULT_BACKEND`  
**Queues:** `default`, `fast-track` (AI workflows), `heavy-lifting` (asset processing), `io-bound` (scrapers/syncs)  
**Sync DB session:** Created per-worker from `_SyncDB_URL` (psycopg2 driver, not asyncpg). Uses `sessionmaker` with a dedicated engine (`_sync_engine`) in `tasks.py`.

---

## Task Architecture

### `BaseJobTask` (abstract)
All workflow tasks extend this. Provides:
- `run(job_id)` — manages DB session, calls `execute()`, handles status lifecycle
- Retry with exponential backoff: 30s → 60s → 120s (max 3 retries)
- Dead-letter strategy: on `MaxRetriesExceededError`, job stays `failed`, worker does NOT crash

### Concrete workflow tasks

| Task name | Class | Workflow |
|---|---|---|
| `app.worker.tasks.run_keyword_to_article` | `_KeywordToArticleTask` | Keyword → Article |
| `app.worker.tasks.run_reference_to_article` | (inline) | Reference URL → Article |

---

## All Registered Tasks

### `run_keyword_to_article`
- Calls `KeywordPipeline(job_id, db, job).run()`
- Registered via `celery_app.register_task(_KeywordToArticleTask())`
- Handled by `fast-track` queue.

### `run_reference_to_article`
- Calls `ReferencePipeline(job_id, db, job).run()`
- Registered via `celery_app.register_task(_ReferenceToArticleTask())`
- Handled by `fast-track` queue.

### `sync_wp_links`
- Fetches all published posts from a WP REST API (`/wp-json/wp/v2/posts`)
- Paginates until all posts fetched
- Stores `{slug: url}` map in `Integration.existing_post_links`
- Called by: Celery Beat (nightly) + `POST /integrations/{id}/sync`

### `publish_scheduled_articles`
- Runs every 5 minutes via Celery Beat
- Finds all `Article` rows with `article_status = scheduled` AND `scheduled_publish_at <= now()`
- For each: loads Integration, calls `WordPressPublisher.publish()`, sets `published` or `publish_failed`
- Featured image: decoded from `article.images[role=featured].base64_image`

---

## Celery Beat Schedule (in `celery_app.py`)

```python
beat_schedule = {
    "publish-scheduled": {
        "task": "app.worker.tasks.publish_scheduled_articles",
        "schedule": crontab(minute="*/5"),
    },
    "sync-wp-links-nightly": {
        "task": "app.worker.tasks.sync_wp_links",
        "schedule": crontab(hour="2", minute="0"),
        # Note: integration_id must be passed — Beat currently runs without args (STUB)
    },
}
```

---

## DB Helper Functions (in `tasks.py`)

```python
_get_job(db, job_id)          # → Job | None
_set_status(db, job, status, progress_pct, meta_update)
_set_processing(db, job)       # status=processing, progress=5
_set_completed(db, job, ...)   # status=completed, progress=100, credits -= 1
_set_failed(db, job, error)    # status=failed, error_message=error[:2000]
```

---

## Adding a New Celery Task

```python
class _MyTask(BaseJobTask):
    def execute(self, job_id: str, db: Session, job: Job) -> dict:
        from app.pipelines.my_pipeline import MyPipeline
        return MyPipeline(job_id=job_id, db=db, job=job).run()

run_my_task = celery_app.register_task(_MyTask())
run_my_task.name = "app.worker.tasks.run_my_task"
```

---

## Monitoring

Celery Flower available at `http://localhost:5555` (enabled in `docker-compose.yml`).  
Inspect tasks: `celery -A app.worker.celery_app inspect active`
