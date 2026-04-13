"""
app/worker/tasks.py — Celery task definitions.

Architecture principles enforced here:
  1. Tasks are THIN SHELLS — they call pipeline modules, they don't implement logic.
  2. Every task wraps its entire body in try/except and updates DB status.
  3. Database access uses a SYNCHRONOUS SQLAlchemy session (not async) because
     Celery workers run in a regular thread/process, not an async event loop.
     We use a separate sync engine created here specifically for the worker.
  4. Retries use exponential backoff via Celery's built-in retry mechanism.
  5. On permanent failure (max retries exceeded), status is set to "failed"
     and the error is logged — the worker does NOT crash.
"""
import asyncio
import uuid
from datetime import datetime, timezone

import structlog
from celery import Task
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from app.config import settings
from app.models.article import Article, ArticleStatus
from app.models.integration import Integration
from app.models.job import Job, JobStatus
from app.models.user import User
from app.worker.celery_app import celery_app

log = structlog.get_logger(__name__)

# ── Synchronous DB engine for Celery workers ──────────────────────────────────
# Replace asyncpg driver with psycopg2 for synchronous access in workers.
_SYNC_DB_URL = settings.database_url.replace(
    "postgresql+asyncpg", "postgresql+psycopg2"
)
_sync_engine = create_engine(
    _SYNC_DB_URL,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
)
_SyncSession = sessionmaker(bind=_sync_engine, autocommit=False, autoflush=False)


# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_job(db: Session, job_id: str) -> Job | None:
    return db.query(Job).filter(Job.id == uuid.UUID(job_id)).first()


def _set_status(
    db: Session,
    job: Job,
    status: str,
    progress_pct: int,
    meta_update: dict | None = None,
) -> None:
    """Generic status setter to avoid repetition."""
    job.status = status
    job.progress_pct = progress_pct
    if meta_update:
        job.meta = {**job.meta, **meta_update}
    db.commit()


def _set_processing(db: Session, job: Job) -> None:
    _set_status(db, job, JobStatus.PROCESSING.value, progress_pct=5)


def _set_completed(db: Session, job: Job, meta_update: dict | None = None) -> None:
    job.status = JobStatus.COMPLETED.value
    job.progress_pct = 100
    job.completed_at = datetime.now(timezone.utc)
    if meta_update:
        job.meta = {**job.meta, **meta_update}

    # Atomically decrement user credits
    user = db.query(User).filter(User.id == job.user_id).first()
    if user and user.credits > 0:
        user.credits = User.credits - 1

    db.commit()


def _set_failed(db: Session, job: Job, error: str) -> None:
    job.status = JobStatus.FAILED.value
    job.error_message = error[:2000]   # cap length for DB column
    job.completed_at = datetime.now(timezone.utc)
    db.commit()


# ── Base task class with shared error handling ────────────────────────────────

class BaseJobTask(Task):
    """
    Abstract Celery task base class.
    Subclasses override `execute(job_id, db, job)` with the actual pipeline.
    """
    abstract = True

    def run(self, job_id: str) -> dict:
        log.info("task.started", task=self.name, job_id=job_id)
        with _SyncSession() as db:
            job = _get_job(db, job_id)
            if job is None:
                log.error("task.job_not_found", task=self.name, job_id=job_id)
                return {"status": "error", "detail": "Job not found"}

            _set_processing(db, job)
            try:
                result_meta = self.execute(job_id=job_id, db=db, job=job)
                _set_completed(db, job, meta_update=result_meta)
                log.info("task.completed", task=self.name, job_id=job_id)
                return {"status": "completed", "job_id": job_id}

            except Exception as exc:
                # ── Dead-letter strategy: log, mark failed, don't crash worker ──
                log.exception(
                    "task.failed",
                    task=self.name,
                    job_id=job_id,
                    error=str(exc),
                )
                _set_failed(db, job, error=str(exc))

                # Attempt Celery retry with exponential backoff
                try:
                    raise self.retry(
                        exc=exc,
                        countdown=2 ** self.request.retries * 30,  # 30, 60, 120s
                        max_retries=3,
                    )
                except self.MaxRetriesExceededError:
                    log.error(
                        "task.max_retries_exceeded",
                        task=self.name,
                        job_id=job_id,
                    )
                    return {"status": "failed", "job_id": job_id}

    def execute(self, job_id: str, db: Session, job: Job) -> dict:
        """Override in concrete task classes. Return a dict to merge into job.meta."""
        raise NotImplementedError


# ── Workflow 1: Keyword → Article ─────────────────────────────────────────────

class _KeywordToArticleTask(BaseJobTask):
    """
    Workflow 1 concrete task: Keyword → Article.

    Inherits all retry logic, DB session management, and status lifecycle
    from BaseJobTask.run(). Only `execute()` contains pipeline-specific code.

    Step sequence (inside KeywordPipeline.run()):
      1. DuckDuckGo search → top-N competitor URLs
      2. Async httpx + trafilatura scraping (best-effort, per-URL)
      3. Gemini: content gap analysis
      4. Gemini: structured article outline
      5. Gemini: section-by-section HTML writing
      6. Imagen: image generation (stub — future work)
      7. bleach sanitise → upsert Article in DB
    """

    def execute(self, job_id: str, db: Session, job: Job) -> dict:
        # Inline import: keeps worker startup fast; avoids loading Vertex AI SDK
        # until this specific task type is actually dispatched.
        from app.pipelines.keyword_pipeline import KeywordPipeline
        return KeywordPipeline(job_id=job_id, db=db, job=job).run()


run_keyword_to_article = celery_app.register_task(
    _KeywordToArticleTask()
)
# Give the registered task a predictable name for routing and monitoring.
run_keyword_to_article.name = "app.worker.tasks.run_keyword_to_article"




# ── Workflow 2: Link → Article ────────────────────────────────────────────────

@celery_app.task(
    bind=True,
    name="app.worker.tasks.process_link_workflow",
    queue="default",
    # Reliability: acknowledge only after execution completes, so message is
    # requeued if the worker process is killed mid-task.
    acks_late=True,
    reject_on_worker_lost=True,
)
def process_link_workflow(self: Task, job_id: str, url: str) -> dict:
    """
    Workflow 2 pipeline: Link → Article.

    State machine:
        PENDING → SCRAPING → WRITING → COMPLETED
                                     ↘ FAILED (on any exception)

    The async scraper (extract_article_content) is bridged to this synchronous
    Celery task via asyncio.run(), which is safe because Celery workers run in
    a plain thread with no running event loop.

    Args:
        job_id: UUID string of the Job row.
        url:    Source article URL (already SSRF-validated at API layer).

    Returns:
        dict with "status" and "job_id" keys (consumed by Celery result backend).
    """
    logger = log.bind(task="process_link_workflow", job_id=job_id, url=url)
    logger.info("workflow2.start")

    # Inline imports keep worker startup fast and prevent circular dependencies.
    from app.scrapers.article_scraper import extract_article_content
    from app.ai.gemini_writer import rewrite_article_for_seo

    with _SyncSession() as db:
        # ── Guard: ensure the Job exists ─────────────────────────────────────
        job = _get_job(db, job_id)
        if job is None:
            logger.error("workflow2.job_not_found")
            return {"status": "error", "detail": f"Job {job_id!r} not found in DB."}

        try:
            # ── Stage 1: SCRAPING ─────────────────────────────────────────────
            logger.info("workflow2.stage.scraping")
            _set_status(
                db, job,
                status="scraping",   # custom granular status visible to the UI
                progress_pct=10,
            )

            # Bridge async → sync: asyncio.run() creates a fresh event loop.
            markdown_content: str = asyncio.run(extract_article_content(url))

            logger.info(
                "workflow2.scraping.done",
                markdown_chars=len(markdown_content),
            )

            # ── Stage 2: WRITING ──────────────────────────────────────────────
            logger.info("workflow2.stage.writing")
            _set_status(
                db, job,
                status="writing",
                progress_pct=40,
            )

            ai_result: dict = rewrite_article_for_seo(markdown_content)
            # ai_result keys: title, html_content, word_count, slug

            logger.info(
                "workflow2.writing.done",
                title=ai_result.get("title"),
                word_count=ai_result.get("word_count"),
            )

            # ── Stage 3: Persist Article ──────────────────────────────────────
            logger.info("workflow2.stage.persisting")
            _set_status(db, job, status="writing", progress_pct=80)

            _upsert_article(db=db, job=job, url=url, ai_result=ai_result)

            # ── Stage 4: COMPLETED ────────────────────────────────────────────
            _set_completed(
                db, job,
                meta_update={
                    "source_url": url,
                    "article_title": ai_result.get("title"),
                    "word_count": ai_result.get("word_count"),
                },
            )
            logger.info("workflow2.completed")
            return {"status": "completed", "job_id": job_id}

        except Exception as exc:
            # ── FAILED: log to error_message column and mark job failed ───────
            error_str = f"{type(exc).__name__}: {exc}"
            logger.exception("workflow2.failed", error=error_str)
            _set_failed(db, job, error=error_str)

            # Retry with exponential back-off (30 → 60 → 120 seconds).
            # On MaxRetriesExceeded the job stays FAILED; the worker does NOT crash.
            try:
                raise self.retry(
                    exc=exc,
                    countdown=2 ** self.request.retries * 30,
                    max_retries=3,
                )
            except self.MaxRetriesExceededError:
                logger.error("workflow2.max_retries_exceeded")
                return {"status": "failed", "job_id": job_id}


# ── Article upsert helper ─────────────────────────────────────────────────────

def _upsert_article(db: Session, job: Job, url: str, ai_result: dict) -> Article:
    """
    Insert a new Article row or update the existing one for this job.

    Using a get-or-create pattern so re-queued tasks don't violate the
    unique constraint on articles.job_id.
    """
    article: Article | None = (
        db.query(Article).filter(Article.job_id == job.id).first()
    )

    if article is None:
        article = Article(
            job_id=job.id,
            user_id=job.user_id,
            integration_id=job.integration_id,
        )
        db.add(article)

    article.title = ai_result["title"]
    article.slug = ai_result.get("slug")
    article.html_content = ai_result["html_content"]
    article.word_count = ai_result.get("word_count", 0)
    article.source_urls = [url]
    article.article_status = ArticleStatus.DRAFT_READY
    article.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(article)
    log.info("workflow2.article_saved", article_id=str(article.id))
    return article


# ═════════════════════════════════════════════════════════════════════════════
# Background maintenance tasks
# ═════════════════════════════════════════════════════════════════════════════

@celery_app.task(
    name="app.worker.tasks.sync_wp_links",
    bind=True,
    max_retries=3,
    default_retry_delay=120,
    queue="default",
)
def sync_wp_links(self, integration_id: str) -> dict:
    """
    Fetch the list of published posts from a WordPress site and cache their
    slugs + URLs into Integration.existing_post_links for internal linking.

    STUB — WordPress REST API calls will be implemented in a follow-up.
    Currently writes a timestamped placeholder so the schema is exercised.

    Called by:
      - Celery Beat schedule (nightly, for every active Integration)
      - Manually via the integrations management API

    Args:
        integration_id: UUID string of the Integration to sync.

    Returns:
        Dict with ``integration_id``, ``post_count``, and ``synced_at`` keys.
    """
    logger = log.bind(task="sync_wp_links", integration_id=integration_id)
    logger.info("sync_wp_links.start")

    with _SyncSession() as db:
        integration: Integration | None = (
            db.query(Integration)
            .filter(Integration.id == uuid.UUID(integration_id))
            .first()
        )
        if integration is None:
            logger.warning("sync_wp_links.integration_not_found")
            return {"error": "Integration not found", "integration_id": integration_id}

        import httpx

        post_links = {}
        page = 1
        per_page = 100
        base_url = integration.wp_url.rstrip("/")
        api_url = f"{base_url}/wp-json/wp/v2/posts"

        try:
            with httpx.Client(timeout=30.0) as client:
                while True:
                    response = client.get(
                        api_url,
                        params={
                            "status": "publish",
                            "_fields": "slug,link",
                            "per_page": per_page,
                            "page": page,
                        }
                    )
                    response.raise_for_status()
                    posts = response.json()

                    if not posts:
                        break

                    for post in posts:
                        if "slug" in post and "link" in post:
                            post_links[post["slug"]] = post["link"]

                    total_pages = int(response.headers.get("X-WP-TotalPages", 1))
                    if page >= total_pages:
                        break
                    page += 1

        except httpx.HTTPError as exc:
            logger.error("sync_wp_links.http_error", error=str(exc))
            return {"error": str(exc), "integration_id": integration_id}
        except Exception as exc:
            logger.error("sync_wp_links.unexpected_error", error=str(exc))
            return {"error": str(exc), "integration_id": integration_id}

        synced_at = datetime.now(timezone.utc).isoformat()
        integration.existing_post_links = post_links
        integration.updated_at = datetime.now(timezone.utc)
        db.commit()

    result = {
        "integration_id": integration_id,
        "post_count": len(post_links),
        "synced_at": synced_at,
    }
    logger.info("sync_wp_links.done", **result)
    return result


@celery_app.task(
    name="app.worker.tasks.publish_scheduled_articles",
    bind=True,
    queue="default",
)
def publish_scheduled_articles(self) -> dict:
    """
    Celery Beat periodic task: publish all articles whose
    ``scheduled_publish_at`` has passed and are still in ``scheduled`` status.

    Configure in celery_app.py beat_schedule:
        "publish-scheduled": {
            "task": "app.worker.tasks.publish_scheduled_articles",
            "schedule": crontab(minute="*/5"),   # every 5 minutes
        }

    Flow for each due article:
      1. Load the article's Integration for WP credentials.
      2. Call WordPressPublisher.publish() synchronously.
      3. On success: set article_status=published, wp_post_id, published_at.
      4. On failure: set article_status=publish_failed, log error.
    """
    logger = log.bind(task="publish_scheduled_articles")
    now = datetime.now(timezone.utc)
    logger.info("publish_scheduled_articles.start", now=now.isoformat())

    from app.models.article import Article, ArticleStatus
    from app.integrations.wordpress import WordPressPublisher, WordPressAuthError
    import asyncio as _asyncio
    import base64 as _b64

    published_ids: list[str] = []
    failed_ids: list[str] = []

    with _SyncSession() as db:
        due_articles = (
            db.query(Article)
            .filter(
                Article.article_status == ArticleStatus.SCHEDULED,
                Article.scheduled_publish_at <= now,
            )
            .all()
        )

        logger.info("publish_scheduled_articles.due_count", count=len(due_articles))

        for article in due_articles:
            art_logger = logger.bind(article_id=str(article.id))
            if not article.integration_id:
                art_logger.warning("publish_scheduled.no_integration")
                article.article_status = ArticleStatus.PUBLISH_FAILED
                db.commit()
                failed_ids.append(str(article.id))
                continue

            integration = (
                db.query(Integration)
                .filter(Integration.id == article.integration_id)
                .first()
            )
            if integration is None:
                art_logger.warning("publish_scheduled.integration_missing")
                article.article_status = ArticleStatus.PUBLISH_FAILED
                db.commit()
                failed_ids.append(str(article.id))
                continue

            publisher = WordPressPublisher(
                wp_site_url=integration.wp_url,
                wp_username=integration.wp_username,
                wp_app_password=integration.wp_password,
            )

            # Resolve featured image bytes
            image_bytes: bytes | None = None
            image_alt: str = article.title
            for img in article.images or []:
                if img.get("role") == "featured" and img.get("base64_image"):
                    image_bytes = _b64.b64decode(img["base64_image"])
                    image_alt = img.get("alt_text", article.title)
                    break

            try:
                publish_result = _asyncio.run(
                    publisher.publish(
                        title=article.title,
                        html_content=article.html_content,
                        image_bytes=image_bytes,
                        image_alt=image_alt,
                        slug=article.slug,
                        meta_description=article.meta_description,
                    )
                )
                article.wp_post_id = publish_result["wp_post_id"]
                article.wp_post_url = publish_result["wp_post_url"]
                if publish_result.get("featured_image_url"):
                    article.featured_image_url = publish_result["featured_image_url"]
                article.published_at = datetime.now(timezone.utc)
                article.scheduled_publish_at = None
                article.article_status = ArticleStatus.PUBLISHED
                db.commit()
                published_ids.append(str(article.id))
                art_logger.info(
                    "publish_scheduled.success",
                    wp_post_id=article.wp_post_id,
                    wp_url=article.wp_post_url,
                )
            except (WordPressAuthError, Exception) as exc:
                art_logger.error("publish_scheduled.failed", error=str(exc))
                article.article_status = ArticleStatus.PUBLISH_FAILED
                db.commit()
                failed_ids.append(str(article.id))

    result = {
        "published": len(published_ids),
        "failed": len(failed_ids),
        "published_ids": published_ids,
        "failed_ids": failed_ids,
    }
    logger.info("publish_scheduled_articles.done", **result)
    return result
