"""
app/models/user.py — User ORM model.

Represents an authenticated user of the SaaS platform.
Passwords are NEVER stored in plaintext — use passlib (bcrypt) before saving.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, String, text, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    # ── Primary Key ───────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    # ── Identity ──────────────────────────────────────────────────────────────
    email: Mapped[str] = mapped_column(
        String(320),     # max valid email length per RFC 5321
        unique=True,
        nullable=False,
        index=True,
    )
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ── Status & Roles ────────────────────────────────────────────────────────
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # ── Subscription / Plan (extend for Stripe integration later) ─────────────
    plan: Mapped[str] = mapped_column(
        String(50), default="free", nullable=False
    )  # e.g. "free" | "pro" | "enterprise"
    credits: Mapped[int] = mapped_column(Integer, default=10, nullable=False)

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
    jobs: Mapped[list["Job"]] = relationship(  # noqa: F821 — forward ref
        "Job",
        back_populates="owner",
        cascade="all, delete-orphan",
        lazy="select",
    )
    integrations: Mapped[list["Integration"]] = relationship( # noqa: F821
        "Integration",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="select",
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r} plan={self.plan!r}>"
