"""Statistics, audit trail and role-based access control."""

from tests.conftest import create_user_headers, sample_key


async def test_stats_overview_and_usage(client, admin_headers):
    await client.post(
        "/api/v1/keys", json={"name": "k1", "api_key": sample_key()}, headers=admin_headers
    )
    await client.get("/api/v1/keys/dispense", headers=admin_headers)

    overview = (await client.get("/api/v1/stats/overview", headers=admin_headers)).json()
    assert overview["total_keys"] == 1
    assert overview["keys_by_status"]["active"] == 1
    assert overview["total_dispenses"] == 1
    assert overview["total_users"] == 1

    usage = (await client.get("/api/v1/stats/usage?days=7", headers=admin_headers)).json()
    assert len(usage) == 1
    assert usage[0]["count"] == 1


async def test_audit_records_actions(client, admin_headers):
    await client.post(
        "/api/v1/keys", json={"name": "k1", "api_key": sample_key()}, headers=admin_headers
    )
    entries = (await client.get("/api/v1/audit", headers=admin_headers)).json()
    actions = {entry["action"] for entry in entries}
    assert {"user.registered", "user.login", "key.registered"} <= actions

    filtered = (
        await client.get(
            "/api/v1/audit", params={"action": "key.registered"}, headers=admin_headers
        )
    ).json()
    assert all(entry["action"] == "key.registered" for entry in filtered)
    assert len(filtered) == 1


async def test_viewer_permissions(client, admin_headers):
    viewer = await create_user_headers(client, admin_headers, "ro@example.com", "viewer")
    # Read: allowed
    assert (await client.get("/api/v1/keys", headers=viewer)).status_code == 200
    assert (await client.get("/api/v1/stats/overview", headers=viewer)).status_code == 200
    # Write / privileged: denied
    response = await client.post(
        "/api/v1/keys", json={"name": "x", "api_key": sample_key()}, headers=viewer
    )
    assert response.status_code == 403
    assert (await client.get("/api/v1/keys/dispense", headers=viewer)).status_code == 403
    assert (await client.get("/api/v1/audit", headers=viewer)).status_code == 403


async def test_manager_permissions(client, admin_headers):
    manager = await create_user_headers(client, admin_headers, "mg@example.com", "manager")
    response = await client.post(
        "/api/v1/keys", json={"name": "mk", "api_key": sample_key()}, headers=manager
    )
    assert response.status_code == 201
    assert (await client.get("/api/v1/keys/dispense", headers=manager)).status_code == 200
    assert (await client.get("/api/v1/audit", headers=manager)).status_code == 403
