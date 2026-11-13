async def test_create_endpoint_returns_secret(client):
    resp = await client.post(
        "/api/v1/endpoints",
        json={"url": "https://example.com/hooks", "description": "demo"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["url"] == "https://example.com/hooks"
    assert body["description"] == "demo"
    assert body["is_active"] is True
    assert "secret" in body and len(body["secret"]) >= 32
    assert "id" in body and "created_at" in body


async def test_get_endpoint_hides_secret(client):
    create = await client.post(
        "/api/v1/endpoints", json={"url": "https://example.com/hooks"}
    )
    endpoint_id = create.json()["id"]

    resp = await client.get(f"/api/v1/endpoints/{endpoint_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == endpoint_id
    assert "secret" not in body


async def test_list_endpoints_pagination(client):
    for i in range(3):
        await client.post(
            "/api/v1/endpoints", json={"url": f"https://example.com/{i}"}
        )
    resp = await client.get("/api/v1/endpoints?skip=0&limit=2")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


async def test_update_endpoint(client):
    create = await client.post(
        "/api/v1/endpoints", json={"url": "https://example.com/hooks"}
    )
    endpoint_id = create.json()["id"]

    resp = await client.patch(
        f"/api/v1/endpoints/{endpoint_id}",
        json={"description": "updated", "is_active": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["description"] == "updated"
    assert body["is_active"] is False


async def test_delete_endpoint(client):
    create = await client.post(
        "/api/v1/endpoints", json={"url": "https://example.com/hooks"}
    )
    endpoint_id = create.json()["id"]

    resp = await client.delete(f"/api/v1/endpoints/{endpoint_id}")
    assert resp.status_code == 204

    resp = await client.get(f"/api/v1/endpoints/{endpoint_id}")
    assert resp.status_code == 404


async def test_get_endpoint_not_found(client):
    resp = await client.get("/api/v1/endpoints/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


async def test_create_endpoint_rejects_invalid_url(client):
    resp = await client.post(
        "/api/v1/endpoints", json={"url": "not-a-url"}
    )
    assert resp.status_code == 422
