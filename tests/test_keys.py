"""API key lifecycle tests."""

from datetime import datetime, timedelta, timezone

from app.domain.enums import KeyCheckResult
from tests.conftest import sample_key


async def _create_key(client, headers, name="key-1", **extra):
    payload = {"name": name, "api_key": sample_key(), **extra}
    response = await client.post("/api/v1/keys", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def test_register_and_list(client, admin_headers):
    created = await _create_key(client, admin_headers)
    assert created["status"] == "active"
    assert created["key_hint"].startswith("…")
    listed = (await client.get("/api/v1/keys", headers=admin_headers)).json()
    assert len(listed) == 1
    assert "api_key" not in listed[0]  # plaintext never leaves via list


async def test_duplicate_key_conflict(client, admin_headers):
    secret = sample_key()
    await client.post(
        "/api/v1/keys", json={"name": "a", "api_key": secret}, headers=admin_headers
    )
    response = await client.post(
        "/api/v1/keys", json={"name": "b", "api_key": secret}, headers=admin_headers
    )
    assert response.status_code == 409


async def test_bad_prefix_rejected(client, admin_headers):
    response = await client.post(
        "/api/v1/keys",
        json={"name": "bad", "api_key": "sk-this-is-not-an-nvidia-key"},
        headers=admin_headers,
    )
    assert response.status_code == 422


async def test_remote_validation_on_register(client, admin_headers, fake_validator):
    await _create_key(client, admin_headers, name="validated", validate_remote=True)
    assert fake_validator.calls == 1
    fake_validator.result = KeyCheckResult.INVALID
    response = await client.post(
        "/api/v1/keys",
        json={"name": "rejected", "api_key": sample_key(), "validate_remote": True},
        headers=admin_headers,
    )
    assert response.status_code == 422


async def test_dispense_lru_and_usage(client, admin_headers):
    key_a = await _create_key(client, admin_headers, name="a")
    key_b = await _create_key(client, admin_headers, name="b")
    first = (await client.get("/api/v1/keys/dispense", headers=admin_headers)).json()
    second = (await client.get("/api/v1/keys/dispense", headers=admin_headers)).json()
    assert first["api_key"].startswith("nvapi-")
    assert {first["key_id"], second["key_id"]} == {key_a["id"], key_b["id"]}
    detail = (await client.get(f"/api/v1/keys/{first['key_id']}", headers=admin_headers)).json()
    assert detail["usage_count"] == 1


async def test_dispense_without_keys_404(client, admin_headers):
    response = await client.get("/api/v1/keys/dispense", headers=admin_headers)
    assert response.status_code == 404


async def test_rotate_swaps_and_links(client, admin_headers):
    old = await _create_key(client, admin_headers, name="rotate-me")
    response = await client.post(
        f"/api/v1/keys/{old['id']}/rotate",
        json={"api_key": sample_key()},
        headers=admin_headers,
    )
    assert response.status_code == 201
    replacement = response.json()
    assert replacement["rotated_from_id"] == old["id"]
    assert replacement["status"] == "active"
    old_now = (await client.get(f"/api/v1/keys/{old['id']}", headers=admin_headers)).json()
    assert old_now["status"] == "revoked"


async def test_revoked_key_not_dispensed(client, admin_headers):
    key = await _create_key(client, admin_headers)
    await client.post(f"/api/v1/keys/{key['id']}/revoke", headers=admin_headers)
    response = await client.get("/api/v1/keys/dispense", headers=admin_headers)
    assert response.status_code == 404


async def test_validate_endpoint_marks_invalid(client, admin_headers, fake_validator):
    key = await _create_key(client, admin_headers)
    fake_validator.result = KeyCheckResult.INVALID
    response = await client.post(f"/api/v1/keys/{key['id']}/validate", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["result"] == "invalid"
    assert body["key"]["status"] == "invalid"
    fake_validator.result = KeyCheckResult.VALID
    body = (
        await client.post(f"/api/v1/keys/{key['id']}/validate", headers=admin_headers)
    ).json()
    assert body["key"]["status"] == "active"  # recovered


async def test_expiry_detection(client, admin_headers):
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    key = await _create_key(client, admin_headers, name="expired", expires_at=past)
    response = await client.post(
        "/api/v1/keys/maintenance/expiry-check", headers=admin_headers
    )
    assert response.status_code == 200
    expired_ids = [k["id"] for k in response.json()]
    assert key["id"] in expired_ids
    assert (await client.get("/api/v1/keys/dispense", headers=admin_headers)).status_code == 404


async def test_expiring_soon_flag(client, admin_headers):
    soon = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    key = await _create_key(client, admin_headers, name="soon", expires_at=soon)
    detail = (await client.get(f"/api/v1/keys/{key['id']}", headers=admin_headers)).json()
    assert detail["expiring_soon"] is True


async def test_delete_key_admin_only(client, admin_headers):
    from tests.conftest import create_user_headers

    manager = await create_user_headers(client, admin_headers, "km@example.com", "manager")
    key = await _create_key(client, manager)
    response = await client.delete(f"/api/v1/keys/{key['id']}", headers=manager)
    assert response.status_code == 403
    response = await client.delete(f"/api/v1/keys/{key['id']}", headers=admin_headers)
    assert response.status_code == 204
    response = await client.get(f"/api/v1/keys/{key['id']}", headers=admin_headers)
    assert response.status_code == 404
