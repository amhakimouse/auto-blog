"""
app/api/v1/auth.py — Authentication endpoints.

Endpoints:
  POST /auth/signup  — register a new user account
  POST /auth/login   — obtain a JWT access token (OAuth2 Password flow)
  GET  /auth/me      — return the current user's profile, plan, and credits
"""

import uuid
from datetime import timedelta

from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, field_serializer, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_active_user
from app.database import get_db
from app.models.user import User
from app.security.auth import create_access_token, get_password_hash, verify_password

router = APIRouter()


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: str | None = None


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str | None
    plan: str
    credits: int

    model_config = {"from_attributes": True}

    @field_serializer("id")
    def serialize_id(self, value: Any) -> str:
        return str(value)


class Token(BaseModel):
    access_token: str
    token_type: str


class SignupResponse(BaseModel):
    access_token: str
    token_type: str
    user: UserResponse


@router.post(
    "/signup", response_model=SignupResponse, status_code=status.HTTP_201_CREATED
)
async def signup(user_in: UserCreate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == user_in.email))
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    user = User(
        email=user_in.email,
        hashed_password=get_password_hash(user_in.password),
        full_name=user_in.full_name,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    access_token_expires = timedelta(minutes=60)
    access_token = create_access_token(
        data={"sub": str(user.id)}, expires_delta=access_token_expires
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": UserResponse(
            id=str(user.id),
            email=user.email,
            full_name=user.full_name,
            plan=user.plan,
            credits=user.credits,
        ),
    }


@router.post("/login", response_model=Token)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(User).where(User.email == form_data.username))
    user = result.scalar_one_or_none()

    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token_expires = timedelta(minutes=60)
    access_token = create_access_token(
        data={"sub": str(user.id)}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}


# ── GET /auth/me ──────────────────────────────────────────────────────────────


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get the current user's profile",
)
async def get_current_user_profile(
    user: User = Depends(get_current_active_user),
) -> UserResponse:
    """
    Returns the authenticated user's id, email, full_name, subscription plan,
    and remaining credit balance. Useful for the frontend to hydrate user state
    on page load or app refresh without forcing a new login.
    """
    return UserResponse(
        id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        plan=user.plan,
        credits=user.credits,
    )
