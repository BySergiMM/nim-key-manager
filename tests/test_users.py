"""User administration and RBAC guard tests."""

from tests.conftest import create_user_headers


async def test_admin_creates_and_lists_users(client, admin_headers):
    await create_user_headers(client, admin_headers, "viewer@example.com", "viewer")
    response = await client.get("/api/v1/users", headers=admin_headers)
    assert response.status_code == 200
    emails = [user["email"] for user in response.json()]
    assert "viewer@example.com" in emails


async def test_admin_updates_role(client, admin_headers):
    await create_user_headers(client, admin_headers, "promote@example.com", "viewer")
    users = (await client.get("/api/v1/users", headers=admin_headers)).json()
    target = next(u for u in users if u["email"] == "promote@example.com")
    response = await client.patch(
        f"/api/v1/users/{target['id']}", json={"role": "manager"}, headers=admin_headers
    )
    assert response.status_code == 200
    assert response.json()["role"] == "manager"


async def test_last_admin_cannot_be_demoted(client, admin_headers):
    me = (await client.get("/api/v1/auth/me", headers=admin_headers)).json()
    response = await client.patch(
        f"/api/v1/users/{me['id']}", json={"role": "viewer"}, headers=admin_headers
    )
    assert response.status_code == 409


async def test_delete_user(client, admin_headers):
    await create_user_headers(client, admin_headers, "todelete@example.com", "viewer")
    users = (await client.get("/api/v1/users", headers=admin_headers)).json()
    target = next(u for u in users if u["email"] == "todelete@example.com")
    response = await client.delete(f"/api/v1/users/{target['id']}", headers=admin_headers)
    assert response.status_code == 204
    response = await client.get(f"/api/v1/users/{target['id']}", headers=admin_headers)
    assert response.status_code == 404


async def test_non_admin_cannot_manage_users(client, admin_headers):
    viewer = await create_user_headers(client, admin_headers, "viewer2@example.com", "viewer")
    response = await client.get("/api/v1/users", headers=viewer)
    assert response.status_code == 403
