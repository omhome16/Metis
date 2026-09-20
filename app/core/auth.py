"""Multi-user authentication (METIS_AUTH_MODE=users).

Three modes, one code path for callers:

* **users** (default) — real accounts. `get_current_user` resolves the
  `Authorization: Bearer <jwt>` to a `User` row; protected routes scope every
  query through `owned_vault` so one account never sees another's library.
* **token** — the original shared-secret gate (`app/core/security.py`
  middleware). Kept for scripts, evals, and single-operator deployments; user
  is None and no scoping happens (the middleware already gated the request).
* **none** — localhost dev, exactly the pre-auth behavior.

The transition rule that makes this safe: in users mode `get_current_user`
never returns None for a protected route — a missing/invalid token is a 401,
not an accidental anonymous request.
"""

import hashlib
import hmac
import secrets
import time
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security.utils import get_authorization_scheme_param
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import User
from app.db.session import get_session

logger = get_logger(__name__)

JWT_ALGORITHM = "HS256"
JWT_TTL_SECONDS = 30 * 24 * 3600  # 30 days

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


def hash_password(password: str) -> str:
    """scrypt (stdlib, no native deps) → 'scrypt$n$r$p$salt$hash', all hex."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verification against a `hash_password` string. Never raises."""
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=32,
        )
        return hmac.compare_digest(expected, bytes.fromhex(digest_hex))
    except (ValueError, TypeError):
        return False


def issue_token(user: User) -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": user.id, "email": user.email, "iat": now, "exp": now + JWT_TTL_SECONDS},
        settings.secret_key,
        algorithm=JWT_ALGORITHM,
    )


def _decode_token(token: str) -> dict:
    return jwt.decode(token, settings.secret_key, algorithms=[JWT_ALGORITHM])


SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user(request: Request, session: SessionDep) -> User | None:
    if settings.auth_mode != "users":
        return None
    authorization = request.headers.get("authorization", "")
    scheme, param = get_authorization_scheme_param(authorization)
    if scheme.lower() != "bearer" or not param:
        raise HTTPException(status_code=401, detail="not authenticated")
    try:
        claims = _decode_token(param)
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="invalid or expired token") from None
    user = await session.get(User, str(claims.get("sub", "")))
    if user is None:
        raise HTTPException(status_code=401, detail="account no longer exists")
    return user


CurrentUser = Annotated[User | None, Depends(get_current_user)]


def require_user(user: CurrentUser) -> User:
    """Guard for routes that only exist in users mode (register/login are open)."""
    if user is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return user


async def owned_vault(session: AsyncSession, user: User | None, name: str):
    """Fetch a vault scoped to the account.

    users mode: the vault must belong to the caller (orphans from before the
    first registration are adopted by that first account — see register).
    token/none modes: unscoped fetch, behavior unchanged.
    """
    from app.db.models import Vault

    vault = (await session.execute(select(Vault).where(Vault.name == name))).scalar_one_or_none()
    if vault is None:
        raise HTTPException(status_code=404, detail="vault not found")
    if user is not None and vault.owner_id not in (None, user.id):
        # Do not reveal other users' vault names with a 403 — a 404 says less.
        raise HTTPException(status_code=404, detail="vault not found")
    return vault


async def vault_scope_guard(session: AsyncSession, user: User | None, name: str) -> None:
    """Raise 404 unless `user` may operate on vault `name` (fetch + discard)."""
    await owned_vault(session, user, name)


async def user_vault_names(session: AsyncSession, user: User | None) -> list[str]:
    """Corpora the caller may see ([] unfiltered marker is handled by callers)."""
    from app.db.models import Vault

    if user is None:
        return []
    rows = (await session.execute(select(Vault.name).where(Vault.owner_id == user.id))).scalars()
    return list(rows)
