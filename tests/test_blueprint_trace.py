def valid_trace():
    return {
        "image_width": 1000,
        "image_height": 600,
        "polygons_px": [[
            {"x": 100, "y": 100},
            {"x": 600, "y": 100},
            {"x": 600, "y": 400},
            {"x": 100, "y": 400},
        ]],
        "scale_points_px": [{"x": 100, "y": 100}, {"x": 300, "y": 100}],
        "known_distance_m": 2.0,
        "origin_px": {"x": 100, "y": 100},
        "y_axis_up": True,
    }


def test_saves_browser_trace_as_metric_floor(client, calibrated_source):
    client.put(f"/api/v1/sources/{calibrated_source}/placement", json={
        "x": 1, "y": 1, "rotation_deg": 0, "fov_deg": 70,
    })
    response = client.post("/api/v1/store/blueprint", json=valid_trace())
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["width_m"] == 5
    assert result["height_m"] == 3
    assert result["store"]["map"]["floor_polygons"][0][0] == {"x": 0.0, "y": 0.0}
    assert result["store"]["map"]["floor_polygons"][0][2] == {"x": 5.0, "y": 3.0}
    assert len(result["store"]["map"]["walls"][0]) == 5
    assert result["store"]["map"]["blueprint_trace"]["scale"]["pixels_per_meter"] == 100
    assert result["invalidated_calibrations"] == 1
    source = client.get(f"/api/v1/sources/{calibrated_source}").json()
    assert source["calibrated"] is False
    assert source["placement"] is None


def test_blueprint_image_is_not_accepted_or_stored(client):
    trace = valid_trace()
    trace["image_data"] = "data:image/png;base64,not-an-image"
    response = client.post("/api/v1/store/blueprint", json=trace)
    assert response.status_code == 200
    saved = response.json()["store"]["map"]["blueprint_trace"]
    assert "image_data" not in saved
    assert saved["source_image"] == {"width": 1000, "height": 600}


def test_rejects_singular_scale(client):
    trace = valid_trace()
    trace["scale_points_px"][1] = trace["scale_points_px"][0]
    response = client.post("/api/v1/store/blueprint", json=trace)
    assert response.status_code == 422
    assert "distinct" in response.json()["detail"]


def test_rejects_incomplete_polygon(client):
    trace = valid_trace()
    trace["polygons_px"] = [[{"x": 1, "y": 1}, {"x": 2, "y": 2}]]
    response = client.post("/api/v1/store/blueprint", json=trace)
    assert response.status_code == 422
    assert "three points" in response.json()["detail"]
