"""
app/api/v1/integrations.py — CRUD endpoints for Integration (Brand Profile).

Endpoints:
  POST   /integrations            — create a new brand profile
  GET    /integrations            — list all brand profiles for the current user
  GET    /integrations/{id}       — retrieve a single brand profile
  PUT    /integrations/{id}       — full update (all writable fields)
  PATCH  /integrations/{id}       — partial update
  DELETE /integrations/{id}       — delete (cascades to jobs & articles via DB)

Security:
  All queries are scoped to `current_user.id` — users can never see or modify
  another user's integrations.

Design:
  - wp_password is accepted as plaintext in the request and stored encrypted
    via the Integration model's `wp_password` setter (Fernet AES-128).
  - wp_password is NEVER returned in any response schema.
  - existing_post_links (the WP link cache) is a read-only field in responses;
    it is populated by the `sync_wp_links` Celery task.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, HttpUrl, field_serializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_active_user
from app.database import get_db
from app.logger import get_logger
from app.models.integration import Integration
from app.models.user import User

log = get_logger(__name__)

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────
# Defined locally to keep the module self-contained; move to app/schemas/ if
# they need to be shared with other modules.


class IntegrationCreate(BaseModel):
    """Request body for creating a new Integration."""

    site_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Human-readable label, e.g. 'TechCorp Blog'",
    )
    wp_url: str = Field(
        ...,
        min_length=10,
        max_length=500,
        description="WordPress site root URL (no trailing slash)",
    )
    wp_username: str = Field(
        ..., min_length=1, max_length=255, description="WordPress user login name"
    )
    wp_password: str = Field(
        ...,
        min_length=1,
        max_length=512,
        description="WordPress Application Password (stored encrypted)",
    )
    brand_voice_prompt: str | None = Field(
        default=None,
        max_length=4000,
        description="Free-text style / tone instructions injected into every article prompt.",
    )


class IntegrationUpdate(BaseModel):
    """Request body for a full PUT update — all fields required."""

    site_name: str = Field(..., min_length=1, max_length=255)
    wp_url: str = Field(..., min_length=10, max_length=500)
    wp_username: str = Field(..., min_length=1, max_length=255)
    wp_password: str = Field(
        ...,
        min_length=1,
        max_length=512,
        description="New WordPress Application Password (stored encrypted)",
    )
    brand_voice_prompt: str | None = Field(default=None, max_length=4000)


class IntegrationPatch(BaseModel):
    """Request body for a partial PATCH update — all fields optional."""

    site_name: str | None = Field(default=None, min_length=1, max_length=255)
    wp_url: str | None = Field(default=None, min_length=10, max_length=500)
    wp_username: str | None = Field(default=None, min_length=1, max_length=255)
    wp_password: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
        description="Provide only if you want to rotate the WordPress Application Password.",
    )
    brand_voice_prompt: str | None = Field(default=None, max_length=4000)


class IntegrationRead(BaseModel):
    """Public representation of an Integration — wp_password is never included."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    user_id: uuid.UUID
    site_name: str
    wp_url: str
    wp_username: str
    is_connected: bool = Field(
        default=False, description="Whether the connection was successfully tested"
    )
    brand_voice_prompt: str | None
    cached_link_count: int = Field(
        default=0,
        description="Number of slug→URL pairs currently cached from WordPress.",
    )
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm_with_link_count(cls, integration: Integration) -> "IntegrationRead":
        """Build the response, computing cached_link_count from the JSONB field."""
        data = {
            "id": integration.id,
            "user_id": integration.user_id,
            "site_name": integration.site_name,
            "wp_url": integration.wp_url,
            "wp_username": integration.wp_username,
            "is_connected": False,
            "brand_voice_prompt": integration.brand_voice_prompt,
            "cached_link_count": len(integration.existing_post_links or {}),
            "created_at": integration.created_at,
            "updated_at": integration.updated_at,
        }
        return cls(**data)


# ── Helper ────────────────────────────────────────────────────────────────────


async def _get_owned_integration(
    integration_id: uuid.UUID,
    user: User,
    db: AsyncSession,
) -> Integration:
    """Fetch an Integration owned by *user* or raise 404."""
    result = await db.execute(
        select(Integration).where(
            Integration.id == integration_id,
            Integration.user_id == user.id,
        )
    )
    integration = result.scalar_one_or_none()
    if integration is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integration not found or does not belong to you.",
        )
    return integration


# ── POST /integrations ────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=IntegrationRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new Brand Profile (WordPress Integration)",
)
async def create_integration(
    payload: IntegrationCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> IntegrationRead:
    """
    Persist a new Integration for the authenticated user.
    The WordPress Application Password is encrypted at rest via Fernet AES-128.
    """
    integration = Integration(
        user_id=user.id,
        site_name=payload.site_name,
        wp_url=payload.wp_url,
        wp_username=payload.wp_username,
        brand_voice_prompt=payload.brand_voice_prompt,
        existing_post_links={},
    )
    # Use the model's encrypted setter
    integration.wp_password = payload.wp_password

    db.add(integration)
    await db.commit()
    await db.refresh(integration)

    log.info(
        "integrations.created",
        integration_id=str(integration.id),
        user_id=str(user.id),
        site_name=integration.site_name,
    )
    return IntegrationRead.from_orm_with_link_count(integration)


# ── GET /integrations ─────────────────────────────────────────────────────────


@router.get(
    "",
    response_model=list[IntegrationRead],
    summary="List all Brand Profiles for the current user",
)
async def list_integrations(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
    limit: Annotated[int, Query(ge=1, le=100, description="Max items to return")] = 50,
    offset: Annotated[int, Query(ge=0, description="Pagination offset")] = 0,
) -> list[IntegrationRead]:
    """Return all Integrations belonging to the authenticated user, newest first."""
    from sqlalchemy import desc

    result = await db.execute(
        select(Integration)
        .where(Integration.user_id == user.id)
        .order_by(desc(Integration.created_at))
        .limit(limit)
        .offset(offset)
    )
    integrations = result.scalars().all()
    return [IntegrationRead.from_orm_with_link_count(i) for i in integrations]


# ── GET /integrations/{integration_id} ───────────────────────────────────────


@router.get(
    "/{integration_id}",
    response_model=IntegrationRead,
    summary="Retrieve a single Brand Profile by ID",
)
async def get_integration(
    integration_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> IntegrationRead:
    integration = await _get_owned_integration(integration_id, user, db)
    return IntegrationRead.from_orm_with_link_count(integration)


# ── PUT /integrations/{integration_id} ───────────────────────────────────────


@router.put(
    "/{integration_id}",
    response_model=IntegrationRead,
    summary="Full update of a Brand Profile (all writable fields must be provided)",
)
async def update_integration(
    integration_id: uuid.UUID,
    payload: IntegrationUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> IntegrationRead:
    integration = await _get_owned_integration(integration_id, user, db)

    integration.site_name = payload.site_name
    integration.wp_url = payload.wp_url
    integration.wp_username = payload.wp_username
    integration.wp_password = payload.wp_password  # encrypts via setter
    integration.brand_voice_prompt = payload.brand_voice_prompt

    await db.commit()
    await db.refresh(integration)

    log.info(
        "integrations.updated", integration_id=str(integration_id), user_id=str(user.id)
    )
    return IntegrationRead.from_orm_with_link_count(integration)


# ── PATCH /integrations/{integration_id} ─────────────────────────────────────


@router.patch(
    "/{integration_id}",
    response_model=IntegrationRead,
    summary="Partial update of a Brand Profile",
)
async def patch_integration(
    integration_id: uuid.UUID,
    payload: IntegrationPatch,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> IntegrationRead:
    integration = await _get_owned_integration(integration_id, user, db)

    if payload.site_name is not None:
        integration.site_name = payload.site_name
    if payload.wp_url is not None:
        integration.wp_url = payload.wp_url
    if payload.wp_username is not None:
        integration.wp_username = payload.wp_username
    if payload.wp_password is not None:
        integration.wp_password = payload.wp_password  # encrypts via setter
    if payload.brand_voice_prompt is not None:
        integration.brand_voice_prompt = payload.brand_voice_prompt

    await db.commit()
    await db.refresh(integration)

    log.info(
        "integrations.patched", integration_id=str(integration_id), user_id=str(user.id)
    )
    return IntegrationRead.from_orm_with_link_count(integration)


# ── DELETE /integrations/{integration_id} ────────────────────────────────────


@router.delete(
    "/{integration_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a Brand Profile and all associated Jobs/Articles (cascade)",
)
async def delete_integration(
    integration_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """
    Permanently delete an Integration.
    The DB cascade (ondelete='CASCADE') will remove all child Jobs and Articles.
    """
    integration = await _get_owned_integration(integration_id, user, db)
    await db.delete(integration)
    await db.commit()
    log.info(
        "integrations.deleted", integration_id=str(integration_id), user_id=str(user.id)
    )


# ── POST /integrations/{integration_id}/test ───────────────────────────────────


@router.post(
    "/{integration_id}/test",
    summary="Test WordPress connection",
)
async def test_integration(
    integration_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> dict:
    """
    Test the WordPress integration credentials by attempting to fetch
    the site info via the REST API.
    """
    integration = await _get_owned_integration(integration_id, user, db)

    from app.integrations.wordpress import WordPressPublisher

    try:
        publisher = WordPressPublisher(
            wp_site_url=integration.wp_url,
            wp_username=integration.wp_username,
            wp_app_password=integration.wp_password,
        )
        await publisher.get_site_info()
        return {"success": True, "message": "Connection successful"}
    except Exception as e:
        log.warning(
            "integrations.test_failed", integration_id=str(integration_id), error=str(e)
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Connection failed: {str(e)}",
        )


@router.post(
    "/{integration_id}/sync",
    summary="Trigger WordPress link sync",
)
async def sync_integration(
    integration_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> dict:
    """
    Trigger the Celery task to sync WordPress post links (slug -> URL map).
    Returns 202 Accepted.
    """
    await _get_owned_integration(integration_id, user, db)

    from app.worker.celery_app import celery_app

    celery_app.send_task(
        "app.worker.tasks.sync_wp_links",
        kwargs={"integration_id": str(integration_id)},
        queue="default",
    )

    log.info("integrations.sync_triggered", integration_id=str(integration_id))
    return {"success": True, "message": "Sync task triggered"}
