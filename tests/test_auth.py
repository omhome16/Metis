"""Auth core + routes.

Pure-python pieces (hashing, JWT) run everywhere; route tests need Postgres and
flip `settings.auth_mode` to "users" (conftest forces "none" globally).
"""

import jwt as pyjwt

from app.core.auth import hash_password, issue_token, verify_password
from app.core.config import settings


def test_password_roundtrip():
    hashed = hash_password("correct horse battery staple")
    assert hashed.startswith("scrypt$")
    assert verify_password("correct horse battery staple", hashed)
    assert not verify_password("wrong password", hashed)


def test_password_hash_is_salted():
    assert hash_password("same") != hash_password("same")


def test_verify_password_garbage_input():
    assert not verify_password("x", "not-a-hash")
    assert not verify_password("x", "")


def test_jwt_roundtrip():
    class FakeUser:
        id = "user-123"
        email = "a@b.c"

    token = issue_token(FakeUser())
    claims = pyjwt.decode(token, settings.secret_key, algorithms=["HS256"])
    assert claims["sub"] == "user-123"
    assert claims["email"] == "a@b.c"


async def test_register_login_me(client, require_db, monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "users")
    await client.post("/api/v1/vaults", json={"name": "auth-test-vault"})

    r = await client.post(
        "/api/v1/auth/register",
        json={"email": "alice@example.com", "password": "longenough1", "display_name": "Alice"},
    )
    assert r.status_code == 201, r.text
    token = r.json()["token"]
    # First account adopted the pre-accounts vault.
    r = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["email"] == "alice@example.com"

    # Duplicate email → 409; wrong password → 401; right password → token.
    dup = await client.post(
        "/api/v1/auth/register", json={"email": "alice@example.com", "password": "longenough1"}
    )
    assert dup.status_code == 409
    bad = await client.post(
        "/api/v1/auth/login", json={"email": "alice@example.com", "password": "wrongwrong1"}
    )
    assert bad.status_code == 401
    ok = await client.post(
        "/api/v1/auth/login", json={"email": "alice@example.com", "password": "longenough1"}
    )
    assert ok.status_code == 200


async def test_vault_scoping_between_users(client, require_db, monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "users")
    name = f"scoped-{__import__('uuid').uuid4().hex[:8]}"
    await client.post("/api/v1/vaults", json={"name": name})

    first = (
        await client.post(
            "/api/v1/auth/register", json={"email": "owner@example.com", "password": "longenough1"}
        )
    ).json()["token"]
    second = (
        await client.post(
            "/api/v1/auth/register", json={"email": "other@example.com", "password": "longenough1"}
        )
    ).json()["token"]

    # Owner sees it, stranger gets a 404 that does not reveal existence.
    assert (
        await client.get(f"/api/v1/vaults/{name}", headers={"Authorization": f"Bearer {first}"})
    ).status_code == 200
    assert (
        await client.get(f"/api/v1/vaults/{name}", headers={"Authorization": f"Bearer {second}"})
    ).status_code == 404
    # Deleting without a token is now impossible in users mode.
    assert (await client.delete(f"/api/v1/vaults/{name}")).status_code == 401
    assert (
        await client.delete(f"/api/v1/vaults/{name}", headers={"Authorization": f"Bearer {first}"})
    ).status_code == 200
