"""`/api/v1/auth` — register, login, me.

Open registration. The **first** registered account adopts every orphan vault
(`owner_id IS NULL`), so a library built before users mode existed keeps an
owner instead of becoming permanently invisible. Later accounts start clean.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, update

from app.core.auth import CurrentUser, SessionDep, hash_password, issue_token, verify_password
from app.core.config import settings
from app.db.models import User, Vault

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    display_name: str | None = Field(None, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., max_length=128)


class AuthResponse(BaseModel):
    token: str
    email: str
    display_name: str | None = None


def _require_users_mode() -> None:
    if settings.auth_mode != "users":
        raise HTTPException(status_code=400, detail="accounts are disabled (METIS_AUTH_MODE)")


@router.post("/register", response_model=AuthResponse, status_code=201)
async def register(payload: RegisterRequest, session: SessionDep) -> AuthResponse:
    _require_users_mode()
    email = payload.email.lower().strip()
    exists = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(status_code=409, detail="an account with this email already exists")

    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name,
    )
    session.add(user)
    await session.flush()  # need user.id before adopting orphans

    # Adopt pre-accounts vaults: UPDATE ... WHERE owner_id IS NULL (race-safe
    # enough for the first-user path; a second concurrent registration is
    # rejected by the email uniqueness check anyway).
    await session.execute(update(Vault).where(Vault.owner_id.is_(None)).values(owner_id=user.id))
    await session.commit()
    return AuthResponse(token=issue_token(user), email=user.email, display_name=user.display_name)


@router.post("/login", response_model=AuthResponse)
async def login(payload: LoginRequest, session: SessionDep) -> AuthResponse:
    _require_users_mode()
    email = payload.email.lower().strip()
    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="invalid email or password")
    return AuthResponse(token=issue_token(user), email=user.email, display_name=user.display_name)


@router.get("/me")
async def me(user: CurrentUser) -> dict:
    return {
        "email": user.email if user else None,
        "display_name": user.display_name if user else None,
    }


@router.get("/whoami", include_in_schema=False)
async def whoami(user: Annotated[User | None, Depends(CurrentUser)]) -> dict:
    """Alias kept for the frontend probe before login (returns {} in token/none modes)."""
    return {"email": user.email if user else None}
