"""Authentication flow tests."""

from tests.conftest import ADMIN


async def test_bootstrap_first_user_is_admin(client):
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": "first@example.com", "password": "Password123!", "role": "viewer"},
    )
    assert response.status_code == 201
    assert response.json()["role"] == "admin"  # bootstrap overrides requested role


async def test_register_requires_admin_after_bootstrap(client, admin_headers):
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": "intruder@example.com", "password": "Password123!"},
    )
    assert response.status_code == 403


async def test_admin_can_register_users(client, admin_headers):
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": "manager@example.com", "password": "Password123!", "role": "manager"},
        headers=admin_headers,
    )
    assert response.status_code == 201
    assert response.json()["role"] == "manager"


async def test_duplicate_email_conflict(client, admin_headers):
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": ADMIN["email"], "password": "Password123!"},
        headers=admin_headers,
    )
    assert response.status_code == 409


async def test_login_and_me(client, admin_headers):
    response = await client.get("/api/v1/auth/me", headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["email"] == ADMIN["email"]


async def test_login_wrong_password(client, admin_headers):
    response = await client.post(
        "/api/v1/auth/login", json={"email": ADMIN["email"], "password": "wrong-password"}
    )
    assert response.status_code == 401


async def test_refresh_token_flow(client, admin_headers):
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": ADMIN["email"], "password": ADMIN["password"]},
    )
    refresh_token = response.json()["refresh_token"]
    response = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert response.status_code == 200
    new_access = response.json()["access_token"]
    response = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {new_access}"}
    )
    assert response.status_code == 200


async def test_access_token_rejected_as_refresh(client, admin_headers):
    access = admin_headers["Authorization"].split()[1]
    response = await client.post("/api/v1/auth/refresh", json={"refresh_token": access})
    assert response.status_code == 401


async def test_me_requires_token(client):
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401
