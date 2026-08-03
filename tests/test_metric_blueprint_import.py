import io
import json
import zipfile


def make_bundle(document, extra=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("floor_polygon.json", json.dumps(document))
        for name, content in (extra or {}).items():
            archive.writestr(name, content)
    return output.getvalue()


def valid_document():
    return {
        "schema_version": 1,
        "coordinate_system": {"units": "meters", "y_axis": "up"},
        "scale": {"known_distance_m": 2.0},
        "polygons": [[
            {"x": -1, "y": 2}, {"x": 4, "y": 2},
            {"x": 4, "y": 5}, {"x": -1, "y": 5},
        ]],
    }


def test_imports_plan_blueprint_digitizer_zip(client, calibrated_source):
    client.put(f"/api/v1/sources/{calibrated_source}/placement", json={
        "x": 1, "y": 1, "rotation_deg": 0, "fov_deg": 70,
    })
    response = client.post(
        "/api/v1/store/import-metric-blueprint",
        files={"bundle": ("metric-blueprint.zip", make_bundle(valid_document()), "application/zip")},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["width_m"] == 5
    assert result["height_m"] == 3
    assert result["store"]["map"]["floor_polygons"][0][0] == {"x": 0.0, "y": 3.0}
    assert len(result["store"]["map"]["walls"][0]) == 5
    assert result["invalidated_calibrations"] == 1
    source = client.get(f"/api/v1/sources/{calibrated_source}").json()
    assert source["calibrated"] is False
    assert source["placement"] is None


def test_rejects_non_metric_plan(client):
    document = valid_document()
    document["coordinate_system"]["units"] = "pixels"
    response = client.post(
        "/api/v1/store/import-metric-blueprint",
        files={"bundle": ("bad.zip", make_bundle(document), "application/zip")},
    )
    assert response.status_code == 422
    assert "metres" in response.json()["detail"]


def test_rejects_unsafe_zip_path(client):
    response = client.post(
        "/api/v1/store/import-metric-blueprint",
        files={"bundle": ("bad.zip", make_bundle(valid_document(), {"../escape": "x"}), "application/zip")},
    )
    assert response.status_code == 422
    assert "unsafe" in response.json()["detail"]
