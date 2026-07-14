"""Project management tests."""

from tests.conftest import sample_key


async def _create_project(client, headers, name="proyecto-1"):
    response = await client.post(
        "/api/v1/projects", json={"name": name, "description": "demo"}, headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_create_and_list(client, admin_headers):
    project = await _create_project(client, admin_headers)
    listed = (await client.get("/api/v1/projects", headers=admin_headers)).json()
    assert [p["id"] for p in listed] == [project["id"]]


async def test_duplicate_name_conflict(client, admin_headers):
    await _create_project(client, admin_headers)
    response = await client.post(
        "/api/v1/projects", json={"name": "proyecto-1"}, headers=admin_headers
    )
    assert response.status_code == 409


async def test_update_project(client, admin_headers):
    project = await _create_project(client, admin_headers)
    response = await client.patch(
        f"/api/v1/projects/{project['id']}",
        json={"name": "renombrado", "description": "nueva"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["name"] == "renombrado"


async def test_assign_key_and_scoped_dispense(client, admin_headers):
    project = await _create_project(client, admin_headers)
    key = (
        await client.post(
            "/api/v1/keys", json={"name": "k", "api_key": sample_key()}, headers=admin_headers
        )
    ).json()
    response = await client.post(
        f"/api/v1/projects/{project['id']}/keys/{key['id']}", headers=admin_headers
    )
    assert response.status_code == 200
    assert response.json()["project_id"] == project["id"]

    scoped = (
        await client.get(
            "/api/v1/keys", params={"project_id": project["id"]}, headers=admin_headers
        )
    ).json()
    assert len(scoped) == 1

    dispensed = await client.get(
        "/api/v1/keys/dispense", params={"project_id": project["id"]}, headers=admin_headers
    )
    assert dispensed.status_code == 200
    assert dispensed.json()["project_id"] == project["id"]


async def test_dispense_empty_project_404(client, admin_headers):
    project = await _create_project(client, admin_headers, name="vacio")
    response = await client.get(
        "/api/v1/keys/dispense", params={"project_id": project["id"]}, headers=admin_headers
    )
    assert response.status_code == 404


async def test_delete_project_unassigns_keys(client, admin_headers):
    project = await _create_project(client, admin_headers)
    key = (
        await client.post(
            "/api/v1/keys",
            json={"name": "k", "api_key": sample_key(), "project_id": project["id"]},
            headers=admin_headers,
        )
    ).json()
    response = await client.delete(f"/api/v1/projects/{project['id']}", headers=admin_headers)
    assert response.status_code == 204
    detail = (await client.get(f"/api/v1/keys/{key['id']}", headers=admin_headers)).json()
    assert detail["project_id"] is None
