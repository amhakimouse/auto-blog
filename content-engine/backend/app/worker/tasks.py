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
from sqlalchemy.orm.attributes import flag_modified

from app.config import settings
from app.models.article import Article, ArticleStatus
from app.models.integration import Integration
from app.models.job import Job, JobStatus
from app.models.user import User
from app.scrapers.article_scraper import ArticleExtractionError
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
        if job.meta is None:
            job.meta = {}
        job.meta.update(meta_update)
        flag_modified(job, "meta")
    db.commit()


def _set_processing(db: Session, job: Job) -> None:
    _set_status(db, job, JobStatus.PROCESSING.value, progress_pct=5)


def _set_completed(db: Session, job: Job, meta_update: dict | None = None) -> None:
    job.status = JobStatus.COMPLETED.value
    job.progress_pct = 100
    job.completed_at = datetime.now(timezone.utc)
    if meta_update:
        if job.meta is None:
            job.meta = {}
        job.meta.update(meta_update)
        flag_modified(job, "meta")

    # Atomically decrement user credits
    user = db.query(User).filter(User.id == job.user_id).first()
    if user and user.credits > 0:
        user.credits = User.credits - 1

    db.commit()


def _set_failed(db: Session, job: Job, error: str, failure_reason: str | None = None) -> None:
    job.status = JobStatus.FAILED.value
    job.error_message = error[:2000]   # cap length for DB column
    job.completed_at = datetime.now(timezone.utc)
    
    if failure_reason:
        if job.meta is None:
            job.meta = {}
        job.meta["failure_reason"] = failure_reason
        flag_modified(job, "meta")
        
    db.commit()


# ── Base task class with shared error handling ────────────────────────────────

class BaseJobTask(Task):
    """
    Abstract Celery task base class.
    Subclasses override `execute(job_id, db, job)` with the actual pipeline.
    """
    abstract = True

    def run(self, job_id: str) -> dict:
        log.info("task.received", task=self.name, job_id=job_id)
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

            except ArticleExtractionError as exc:
                log.warning(
                    "task.extraction_failed",
                    task=self.name,
                    job_id=job_id,
                    error=str(exc),
                )
                # Store user-friendly failure reason in job.meta
                failure_reason = str(exc)
                if "trafilatura" in failure_reason.lower() or "no usable content" in failure_reason.lower():
                    failure_reason = "This site blocks automated access or uses JavaScript-only rendering."
                
                _set_failed(db, job, error=str(exc), failure_reason=failure_reason)
                return {"status": "failed", "job_id": job_id}

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
    name = "app.worker.tasks.run_keyword_to_article"

    def execute(self, job_id: str, db: Session, job: Job) -> dict:
        # Inline import: keeps worker startup fast; avoids loading Vertex AI SDK
        # until this specific task type is actually dispatched.
        from app.pipelines.keyword_pipeline import KeywordPipeline
        return KeywordPipeline(job_id=job_id, db=db, job=job).run()


run_keyword_to_article = celery_app.register_task(
    _KeywordToArticleTask()
)




# ── Workflow 2: Link → Article ────────────────────────────────────────────────

class _ReferenceToArticleTask(BaseJobTask):
    """
    Workflow 2 concrete task: Reference URL → Article.
    """
    name = "app.worker.tasks.run_reference_to_article"

    def execute(self, job_id: str, db: Session, job: Job) -> dict:
        from app.pipelines.reference_pipeline import ReferencePipeline
        return ReferencePipeline(job_id=job_id, db=db, job=job).run()

run_reference_to_article = celery_app.register_task(
    _ReferenceToArticleTask()
)


# ── Parallel Asset Processing ────────────────────────────────────────────────

@celery_app.task(
    name="app.worker.tasks.process_single_asset",
    bind=True,
    max_retries=2,
    queue="heavy-lifting",
)
def process_single_asset(self, p_data: dict, integration_id: str | None = None) -> dict:
    """
    Atomic task: 
    1. Generate via Imagen
    2. Convert to WebP
    3. Upload to WP (if integrated)
    """
    logger = log.bind(task="process_single_asset", prompt=p_data.get("title"))
    logger.info("asset_task.start")
    
    from app.ai.imagen_client import generate_section_image
    from app.utils.image import convert_to_webp
    from app.integrations.wordpress import WordPressPublisher, is_wordpress_configured
    import asyncio as _asyncio
    import base64 as _b64

    try:
        # 1. Generate
        img_res = generate_section_image(p_data["prompt"], aspect_ratio="16:9")
        
        # 2. Convert
        webp_bytes = convert_to_webp(img_res["base64_image"])
        
        # 3. WP Upload (speculative)
        wp_url = None
        if integration_id:
            with _SyncSession() as db:
                integration = db.query(Integration).filter(Integration.id == uuid.UUID(integration_id)).first()
                if integration and is_wordpress_configured(integration.wp_url, integration.wp_username, integration.wp_password):
                    publisher = WordPressPublisher(integration.wp_url, integration.wp_username, integration.wp_password)
                    # We create a temporary client here for sync execution
                    try:
                        upload_res = _asyncio.run(publisher._upload_media(
                            client=None, # _upload_media handles client creation if None
                            image_bytes=webp_bytes,
                            filename=f"asset_{uuid.uuid4().hex[:8]}.webp",
                            alt_text=p_data.get("alt_text", ""),
                            caption=p_data.get("caption", ""),
                            description=p_data.get("description", ""),
                        ))
                        wp_url = upload_res.get("source_url")
                    except Exception as wp_exc:
                        logger.warning("asset_task.wp_upload_failed", error=str(wp_exc))

        logger.info("asset_task.success", wp_url=wp_url)
        return {
            "status": "success",
            "webp_base64": _b64.b64encode(webp_bytes).decode("utf-8"),
            "wp_url": wp_url,
            "metadata": p_data
        }

    except Exception as exc:
        logger.error("asset_task.failed", error=str(exc))
        # Fail gracefully: return partial data so the pipeline can continue
        return {
            "status": "failed",
            "error": str(exc),
            "metadata": p_data
        }


# ── Detached Final Publish ───────────────────────────────────────────────────

@celery_app.task(
    name="app.worker.tasks.publish_article_to_wp",
    bind=True,
    max_retries=3,
    queue="io-bound",
)
def publish_article_to_wp(self, article_id: str) -> dict:
    """
    Detached task to handle WordPress posting and RankMath sync.
    Marked as DRAFT initially for safety.
    """
    logger = log.bind(task="publish_article_to_wp", article_id=article_id)
    logger.info("publish_task.start")
    
    from app.integrations.wordpress import WordPressPublisher, is_wordpress_configured
    import asyncio as _asyncio
    import base64 as _b64

    with _SyncSession() as db:
        article = db.query(Article).filter(Article.id == uuid.UUID(article_id)).first()
        if not article or not article.integration_id:
            logger.error("publish_task.aborted", reason="Article or Integration missing")
            return {"status": "error"}

        integration = db.query(Integration).filter(Integration.id == article.integration_id).first()
        if not integration:
            return {"status": "error"}

        publisher = WordPressPublisher(integration.wp_url, integration.wp_username, integration.wp_password)
        
        try:
            # Featured image bytes if available
            # ... (logic to find featured image from article.images if stored) ...
            
            pub_result = _asyncio.run(publisher.publish(
                title=article.title,
                html_content=article.html_content,
                slug=article.slug,
                meta_description=article.meta_description,
                focus_keyword=article.focus_keyword,
                post_status="draft"
            ))
            
            article.wp_post_id = pub_result["wp_post_id"]
            article.wp_post_url = pub_result["wp_post_url"]
            article.article_status = ArticleStatus.PUBLISHED
            db.commit()
            
            logger.info("publish_task.success", post_id=article.wp_post_id)
            return {"status": "success", "post_id": article.wp_post_id}
            
        except Exception as exc:
            logger.error("publish_task.failed", error=str(exc))
            article.article_status = ArticleStatus.PUBLISH_FAILED
            db.commit()
            raise self.retry(exc=exc, countdown=300)

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
