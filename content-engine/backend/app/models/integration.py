"""
app/models/integration.py — Integration (Brand Profile) ORM model.

One User → many Integrations. Each Integration represents one target
WordPress site with its own brand voice, credentials, and cached link index.

Fields:
  site_name              — Human-readable label (e.g. "Client Blog - TechCorp")
  wp_url                 — WordPress site URL (no trailing slash)
  wp_username            — WordPress username
  wp_password_encrypted  — AES-Fernet encrypted WordPress App Password
  brand_voice_prompt     — Free-text prompt injected into every Gemini section
                           call for this site (tone, style, persona, restrictions)
  existing_post_links    — JSONB cache of {slug → url} pairs for internal linking.
                           Populated by the `sync_wp_links` Celery task.
"""
from __future__ import annotations

import base64
import uuid
from datetime import datetime, timezone

from cryptography.fernet import Fernet
from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import settings
from app.database import Base


def _get_fernet() -> Fernet:
    """Derive a 32-byte Fernet key from settings.secret_key (stable, no new env var)."""
    key_bytes = settings.secret_key.encode("utf-8")
    key_bytes = key_bytes[:32] if len(key_bytes) >= 32 else key_bytes.ljust(32, b"0")
    return Fernet(base64.urlsafe_b64encode(key_bytes))


class Integration(Base):
    __tablename__ = "integrations"

    # ── Primary Key ───────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    # ── Foreign Key ───────────────────────────────────────────────────────────
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Site identity ─────────────────────────────────────────────────────────
    site_name: Mapped[str] = mapped_column(String(255), nullable=False)
    wp_url: Mapped[str] = mapped_column(String(500), nullable=False)
    wp_username: Mapped[str] = mapped_column(String(255), nullable=False)
    wp_password_encrypted: Mapped[str] = mapped_column(String(1024), nullable=False)
    is_connected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("FALSE"))
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Brand voice ───────────────────────────────────────────────────────────
    # Injected verbatim into the Gemini section-writer system prompt so every
    # article produced for this integration has the correct tone/style.
    brand_voice_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Internal link cache ───────────────────────────────────────────────────
    # Schema: {"slug": "full-url", ...}
    # Populated / refreshed by the `sync_wp_links` Celery task.
    existing_post_links: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    user: Mapped["User"] = relationship(  # noqa: F821
        "User", back_populates="integrations"
    )
    jobs: Mapped[list["Job"]] = relationship(  # noqa: F821
        "Job",
        back_populates="integration",
        cascade="all, delete-orphan",
        lazy="select",
    )
    articles: Mapped[list["Article"]] = relationship(  # noqa: F821
        "Article",
        back_populates="integration",
        cascade="all, delete-orphan",
        lazy="select",
    )

    # ── Encrypted password property ───────────────────────────────────────────

    @property
    def wp_password(self) -> str:
        """Decrypt wp_password_encrypted and return as plaintext."""
        return _get_fernet().decrypt(
            self.wp_password_encrypted.encode("utf-8")
        ).decode("utf-8")

    @wp_password.setter
    def wp_password(self, plaintext_pw: str) -> None:
        """Encrypt *plaintext_pw* and store in wp_password_encrypted."""
        self.wp_password_encrypted = _get_fernet().encrypt(
            plaintext_pw.encode("utf-8")
        ).decode("utf-8")

    def __repr__(self) -> str:
        return (
            f"<Integration id={self.id} site_name={self.site_name!r} "
            f"wp_url={self.wp_url!r}>"
        )
