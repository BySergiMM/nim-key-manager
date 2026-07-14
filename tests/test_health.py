"""Health, metrics, dashboard and OpenAPI availability."""


async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "up"
    assert body["version"]


async def test_metrics_exposed(client):
    response = await client.get("/metrics")
    assert response.status_code == 200


async def test_dashboard_served(client):
    response = await client.get("/")
    assert response.status_code == 200
    assert "NIM Key Manager" in response.text


async def test_openapi_available(client):
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    assert response.json()["info"]["title"]
