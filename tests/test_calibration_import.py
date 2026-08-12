"""Provider-neutral and NVIDIA-style 3x4 calibration imports."""

import pytest


WORLD = {"name": "warehouse_world", "axes": {"x": "right", "y": "forward", "z": "up"}}


def test_nvidia_projection_import_derives_inverse_floor_homography(client, source_id):
    # u = 100*x + 10, v = 100*y + 20 on z=0.
    projection = [[100, 0, 0, 10], [0, 100, 0, 20], [0, 0, 0, 1]]
    response = client.post("/api/v1/calibrations/import", json={
        "source_id": source_id, "provider": "nvidia_mv3dt",
        "projection_matrix": projection, "units": "m", "world_frame": WORLD,
        "frame_w": 1920, "frame_h": 1080,
        "verification_points": [{"world": [2, 3, 0], "pixel": [210, 320]}],
    })
    assert response.status_code == 201, response.text
    calibration = response.json()
    assert calibration["verification"]["max_reprojection_error_px"] == pytest.approx(0)
    projected = client.post(f"/api/v1/sources/{source_id}/project", json={
        "points": [{"x": 210, "y": 320}],
    }).json()["points"][0]
    assert projected == pytest.approx({"x": 2, "y": 3})


def test_calibration_import_rejects_unknown_units_and_singular_ground_plane(client, source_id):
    bad_units = client.post("/api/v1/calibrations/import", json={
        "source_id": source_id, "projection_matrix": list(range(12)),
        "units": "feet", "world_frame": WORLD,
    })
    assert bad_units.status_code == 422
    singular = client.post("/api/v1/calibrations/import", json={
        "source_id": source_id, "projection_matrix": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]],
        "units": "m", "world_frame": WORLD,
    })
    assert singular.status_code == 422


def test_world_to_map_transform_is_preserved_and_used(client, source_id):
    projection = [[100, 0, 0, 10], [0, 100, 0, 20], [0, 0, 0, 1]]
    transform = [[1, 0, 80], [0, 1, 100], [0, 0, 1]]
    response = client.post("/api/v1/calibrations/import", json={
        "source_id": source_id, "provider": "nvidia_mv3dt",
        "projection_matrix": projection, "world_to_map_transform": transform,
        "units": "m", "world_frame": WORLD,
        "verification_points": [{"world": [82, 103, 0], "pixel": [210, 320]}],
    })
    assert response.status_code == 201, response.text
    calibration = response.json()
    assert calibration["original_projection_matrix"] == projection
    assert calibration["world_to_map_transform"] == transform
    assert calibration["verification"]["max_reprojection_error_px"] == pytest.approx(0)
    projected = client.post(f"/api/v1/sources/{source_id}/project", json={
        "points": [{"x": 210, "y": 320}],
    }).json()["points"][0]
    assert projected == pytest.approx({"x": 82, "y": 103})
