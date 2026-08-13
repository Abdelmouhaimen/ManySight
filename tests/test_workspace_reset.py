from helpers import make_detection


def _zone(client, name="Old zone"):
    return client.post("/api/v1/zones", json={
        "name": name, "polygon": [{"x": 0, "y": 0}, {"x": 3, "y": 0},
                                    {"x": 3, "y": 3}, {"x": 0, "y": 3}],
    }).json()


def test_keep_history_space_reset_versions_evidence_and_breaks_old_zone_reference(client, source_id, isolated_db):
    isolated_db.ex(
        "INSERT INTO source_credentials (source_id,encrypted_payload,credential_type,created_at,updated_at) "
        "VALUES (?,?,?,?,?)", (source_id, "opaque-test-ciphertext", "username,password", 1, 1))
    zone = _zone(client)
    query = client.post("/api/v1/queries", json={
        "name": "Old zone people", "subject": "detection", "measures": ["observations"],
        "filters": {"zone_ids": [zone["id"]]}, "grouping": {},
    }).json()
    posted = client.post("/api/v1/observations/batch", json={
        "observations": [make_detection(source_id, "before-reset", 1000, point_px=None,
                                         geometry={"point_map": {"x": 1, "y": 1}})]
    })
    assert posted.status_code == 200
    old_revision = client.get("/api/v1/store").json()["space_revision_id"]
    isolated_db.ex(
        "INSERT INTO zone_occupancy_observations "
        "(group_id,zone_id,entity_type,ts,value,quality,provenance_json,space_revision_id,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (77, zone["id"], "person", 1000, 1, "known", "{}", old_revision, 1000),
    )
    reset = client.post("/api/v1/workspace/reinitialize-space", json={
        "confirmation": "REINITIALIZE SPACE", "history": "keep",
    })
    assert reset.status_code == 200, reset.text
    assert reset.json()["space_revision_id"] != old_revision
    assert client.get("/api/v1/observations").json()["total"] == 0
    historical = client.get("/api/v1/observations?include_previous_space=true").json()
    assert historical["total"] == 1
    assert historical["observations"][0]["space_revision_id"] == old_revision
    derived = isolated_db.q1("SELECT * FROM zone_occupancy_observations WHERE group_id=77")
    assert derived["space_revision_id"] == old_revision
    unresolved = client.post(f"/api/v1/queries/{query['id']}/execute")
    assert unresolved.status_code == 409
    assert unresolved.json()["detail"]["code"] == "unresolved_query_reference"
    replacement = _zone(client, "Old zone")
    assert replacement["id"] != zone["id"]
    assert client.post(f"/api/v1/queries/{query['id']}/execute").status_code == 409
    assert isolated_db.q1("SELECT source_id FROM source_credentials WHERE source_id=?", (source_id,))


def test_observation_reset_retains_configuration_and_clears_evidence(client, source_id):
    zone = _zone(client)
    query = client.post("/api/v1/queries", json={
        "name": "People", "subject": "detection", "measures": ["observations"],
        "filters": {}, "grouping": {},
    }).json()
    rule = client.post("/api/v1/alert-rules", json={
        "name": "query threshold", "kind": "query_condition", "params": {"query_id": query["id"]},
        "condition": {"operator": ">=", "value": 1},
    }).json()
    client.post("/api/v1/observations/batch", json={
        "observations": [make_detection(source_id, "reset-me", 1000)]
    })
    reset = client.post("/api/v1/workspace/reinitialize-observations", json={
        "confirmation": "REINITIALIZE OBSERVATIONS",
    })
    assert reset.status_code == 200
    assert client.get("/api/v1/observations?include_previous_space=true").json()["total"] == 0
    assert client.get(f"/api/v1/zones/{zone['id']}").status_code == 200
    assert client.get(f"/api/v1/queries/{query['id']}").status_code == 200
    assert client.get("/api/v1/alert-rules").json()[0]["id"] == rule["id"]


def test_delete_history_space_reset_keeps_source_credentials_and_removes_evidence(
        client, source_id, isolated_db):
    isolated_db.ex(
        "INSERT INTO source_credentials (source_id,encrypted_payload,credential_type,created_at,updated_at) "
        "VALUES (?,?,?,?,?)", (source_id, "opaque-test-ciphertext", "token", 1, 1))
    zone = _zone(client)
    client.post("/api/v1/observations/batch", json={
        "observations": [make_detection(source_id, "delete-me", 1000, point_px=None,
                                         geometry={"point_map": {"x": 1, "y": 1}})]
    })
    reset = client.post("/api/v1/workspace/reinitialize-space", json={
        "confirmation": "REINITIALIZE SPACE", "history": "delete",
    })
    assert reset.status_code == 200
    assert len(client.get("/api/v1/sources").json()) == 1
    assert client.get("/api/v1/zones").json() == []
    assert client.get("/api/v1/observations?include_previous_space=true").json()["total"] == 0
    assert isolated_db.q1("SELECT source_id FROM source_credentials WHERE source_id=?", (source_id,))
