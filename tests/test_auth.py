"""Tests for API key authentication and key management."""

from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.cli as cli
from app.core.security import (
    DISPLAY_PREFIX_LENGTH,
    display_prefix,
    generate_api_key,
    hash_api_key,
)
from app.models.api_key import ApiKey

PROTECTED = [
    ("GET", "/api/v1/endpoints"),
    ("POST", "/api/v1/endpoints/00000000-0000-0000-0000-000000000000/events"),
    ("GET", "/api/v1/events/00000000-0000-0000-0000-000000000000"),
    ("GET", "/api/v1/dead-letters"),
    ("GET", "/api/v1/api-keys"),
]

# --- key material ------------------------------------------------------------


def test_generated_keys_are_prefixed_random_and_long():
    keys = {generate_api_key() for _ in range(50)}
    assert len(keys) == 50
    for key in keys:
        assert key.startswith("hr_")
        assert len(key) == 46  # "hr_" + 43 chars of token_urlsafe(32)


def test_hash_is_sha256_hex_and_deterministic():
    key = generate_api_key()
    assert hash_api_key(key) == hash_api_key(key)
    assert len(hash_api_key(key)) == 64
    assert hash_api_key(key) != hash_api_key(generate_api_key())


def test_display_prefix_does_not_reveal_the_key():
    key = generate_api_key()
    assert display_prefix(key) == key[:DISPLAY_PREFIX_LENGTH]
    assert len(display_prefix(key)) == 11


# --- enforcement ---------------------------------------------------------------


@pytest.mark.parametrize(("method", "path"), PROTECTED)
async def test_protected_routes_require_a_key(anon_client, redis_client, method, path):
    resp = await anon_client.request(method, path, json={})
    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "header",
    [
        "Bearer hr_not-a-real-key",
        "Basic dXNlcjpwYXNz",
        "Bearer",
        "hr_without_scheme",
    ],
)
async def test_bad_credentials_are_rejected(anon_client, header):
    resp = await anon_client.get(
        "/api/v1/dead-letters", headers={"Authorization": header}
    )
    assert resp.status_code == 401


async def test_valid_key_is_accepted(anon_client, api_key):
    resp = await anon_client.get(
        "/api/v1/dead-letters", headers={"Authorization": f"Bearer {api_key}"}
    )
    assert resp.status_code == 200


@pytest.mark.parametrize("path", ["/health", "/health/ready", "/metrics"])
async def test_ops_endpoints_stay_open(anon_client, redis_client, path):
    resp = await anon_client.get(path)
    assert resp.status_code == 200


async def test_openapi_declares_bearer_scheme(anon_client):
    spec = (await anon_client.get("/openapi.json")).json()
    schemes = spec["components"]["securitySchemes"]
    assert any(
        s["type"] == "http" and s["scheme"] == "bearer" for s in schemes.values()
    )


# --- key management ------------------------------------------------------------


async def test_create_returns_key_once_and_stores_only_its_hash(client, db_engine):
    resp = await client.post("/api/v1/api-keys", json={"name": "billing-service"})
    assert resp.status_code == 201
    body = resp.json()
    key = body["key"]
    assert key.startswith("hr_")
    assert body["prefix"] == key[:DISPLAY_PREFIX_LENGTH]
    assert body["name"] == "billing-service"
    assert body["revoked_at"] is None

    Session = async_sessionmaker(db_engine)
    async with Session() as session:
        stored = await session.get(ApiKey, UUID(body["id"]))
        assert stored.key_hash == hash_api_key(key)
        # The plaintext appears nowhere in the row.
        row_values = [str(getattr(stored, c.key)) for c in ApiKey.__table__.columns]
        assert all(key not in value for value in row_values)


async def test_created_key_authenticates(client, anon_client):
    key = (await client.post("/api/v1/api-keys", json={"name": "ci"})).json()["key"]
    resp = await anon_client.get(
        "/api/v1/endpoints", headers={"Authorization": f"Bearer {key}"}
    )
    assert resp.status_code == 200


async def test_list_never_returns_keys(client):
    await client.post("/api/v1/api-keys", json={"name": "ci"})

    resp = await client.get("/api/v1/api-keys")
    assert resp.status_code == 200
    names = [k["name"] for k in resp.json()]
    assert names[0] == "ci"  # newest first; the fixture's key is older
    assert "tests" in names
    assert all("key" not in k and "key_hash" not in k for k in resp.json())


async def test_revoked_key_is_rejected_on_next_request(client, anon_client):
    created = (await client.post("/api/v1/api-keys", json={"name": "old"})).json()
    headers = {"Authorization": f"Bearer {created['key']}"}
    assert (
        await anon_client.get("/api/v1/endpoints", headers=headers)
    ).status_code == 200

    resp = await client.delete(f"/api/v1/api-keys/{created['id']}")
    assert resp.status_code == 204

    assert (
        await anon_client.get("/api/v1/endpoints", headers=headers)
    ).status_code == 401
    listed = {k["id"]: k for k in (await client.get("/api/v1/api-keys")).json()}
    assert listed[created["id"]]["revoked_at"] is not None


async def test_revoke_is_idempotent_and_404_for_unknown(client):
    created = (await client.post("/api/v1/api-keys", json={"name": "x"})).json()
    first = await client.delete(f"/api/v1/api-keys/{created['id']}")
    again = await client.delete(f"/api/v1/api-keys/{created['id']}")
    assert first.status_code == again.status_code == 204

    missing = await client.delete(
        "/api/v1/api-keys/00000000-0000-0000-0000-000000000000"
    )
    assert missing.status_code == 404


@pytest.mark.parametrize("name", ["", "x" * 101])
async def test_create_validates_name(client, name):
    resp = await client.post("/api/v1/api-keys", json={"name": name})
    assert resp.status_code == 422


# --- CLI -----------------------------------------------------------------------


@pytest.fixture
def cli_on_test_db(db_engine, monkeypatch):
    """Point the CLI's session factory at the per-test database."""
    Session = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr(cli, "SessionLocal", Session)
    return Session


async def test_cli_creates_a_working_key(cli_on_test_db, anon_client, capsys):
    assert await cli._create_api_key("admin") == 0

    printed = capsys.readouterr().out.strip().splitlines()
    key = printed[-1]
    assert key.startswith("hr_")
    assert "cannot be shown again" in printed[1]

    async with cli_on_test_db() as session:
        stored = (
            await session.execute(select(ApiKey).where(ApiKey.name == "admin"))
        ).scalar_one()
    assert stored.key_hash == hash_api_key(key)

    resp = await anon_client.get(
        "/api/v1/endpoints", headers={"Authorization": f"Bearer {key}"}
    )
    assert resp.status_code == 200


async def test_cli_lists_keys_without_revealing_them(cli_on_test_db, api_key, capsys):
    assert await cli._list_api_keys() == 0
    out = capsys.readouterr().out
    assert display_prefix(api_key) in out
    assert api_key not in out


def test_cli_requires_a_command():
    with pytest.raises(SystemExit):
        cli.main([])
