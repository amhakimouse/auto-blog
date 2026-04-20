"""
app/worker/celery_app.py — Celery application factory.

This module is imported by BOTH the FastAPI backend (to dispatch tasks)
and the Celery worker process (to discover and execute tasks).

Key configuration choices:
  - task_acks_late=True: task is only acknowledged AFTER it completes,
    preventing message loss if the worker crashes mid-execution.
  - task_reject_on_worker_lost=True: companion to acks_late; requeues
    the task if the worker process is killed unexpectedly.
  - Two queues: 'default' for normal jobs, 'high_priority' for future use.
  - Result TTL: 24 hours (results stored in Redis db 1).
"""
from celery import Celery

from app.config import settings

celery_app = Celery("content_engine")

celery_app.conf.update(
    # ── Broker & Backend ─────────────────────────────────────────────────────
    broker_url=settings.celery_broker_url,
    result_backend=settings.celery_result_backend,

    # ── Serialization ────────────────────────────────────────────────────────
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,

    # ── Reliability ──────────────────────────────────────────────────────────
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,    # only grab one task at a time per worker

    # ── Result TTL ───────────────────────────────────────────────────────────
    result_expires=86_400,           # 24 hours

    # ── Queues ───────────────────────────────────────────────────────────────
    task_default_queue="default",
    task_queues={
        "default": {"exchange": "default", "routing_key": "default"},
        "fast-track": {"exchange": "fast-track", "routing_key": "fast-track"},      # Analysis & HTML (LLM)
        "heavy-lifting": {"exchange": "heavy-lifting", "routing_key": "heavy-lifting"}, # Image Gen (Imagen)
        "io-bound": {"exchange": "io-bound", "routing_key": "io-bound"},           # WP Sync & Scrapers
    },
    
    # ── Task Routing ─────────────────────────────────────────────────────────
    task_routes={
        "app.worker.tasks.run_keyword_to_article": {"queue": "fast-track"},
        "app.worker.tasks.run_reference_to_article": {"queue": "fast-track"},
        "app.worker.tasks.process_single_asset": {"queue": "heavy-lifting"},
        "app.worker.tasks.publish_article_to_wp": {"queue": "io-bound"},
    },

    # ── Task autodiscovery ───────────────────────────────────────────────────
    # Celery will scan these modules for @celery_app.task decorators
    imports=["app.worker.tasks"],

    # ── Retry / Dead Letter ──────────────────────────────────────────────────
    task_max_retries=3,
    task_default_retry_delay=60,     # seconds before retry (overridden per task)

    # ── Celery Beat periodic tasks ───────────────────────────────────────────
    beat_schedule={
        # Check every 5 minutes for articles whose scheduled_publish_at has passed
        "publish-scheduled-articles": {
            "task": "app.worker.tasks.publish_scheduled_articles",
            "schedule": 300.0,   # seconds
        },
        # Check every hour for drip-feed scheduled jobs whose execute_at has passed
        "process-scheduled-jobs": {
            "task": "app.worker.tasks.process_scheduled_jobs",
            "schedule": 3600.0,  # runs every hour (3600 seconds)
        },
    },
)
