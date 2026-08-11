"""Worker SDK contract helpers."""

from sdk.python.storelens import StoreLens


def test_detection_frame_count_has_no_analytics_window_metadata():
    client = StoreLens(batch_size=100)
    client.submit_detection_frame(
        source_id=7,
        entity_type="person",
        count=0,
        ts=1234.25,
        observation_id="frame-count-1",
    )

    observation = client._obs_buffer.pop()
    assert observation["kind"] == "measurement"
    assert observation["name"] == "detection_frame_count"
    assert observation["label"] == "person"
    assert observation["value"] == 0
    assert observation["timestamp"] == 1234.25
    assert "attributes" not in observation
    assert "window_s" not in observation


def test_detection_frame_count_rejects_negative_values():
    client = StoreLens(batch_size=100)

    try:
        client.submit_detection_frame(source_id=7, entity_type="person", count=-1)
    except ValueError as exc:
        assert str(exc) == "detection frame count must be non-negative"
    else:
        raise AssertionError("negative frame counts must be rejected")
