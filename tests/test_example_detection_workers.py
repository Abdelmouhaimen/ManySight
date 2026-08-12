"""Representative continuous detection-worker frame semantics."""

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_heatmap_example():
    spec = importlib.util.spec_from_file_location(
        "storelens_heatmap_example",
        ROOT / "examples" / "heatmap_tracker.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeStoreLens:
    def __init__(self):
        self.rows = []

    def submit_detection(self, **payload):
        self.rows.append(("detection", payload))

    def submit_detection_frame(self, **payload):
        self.rows.append(("frame", payload))


def test_example_submits_detections_then_nonzero_frame_marker_with_one_timestamp():
    fake = FakeStoreLens()
    load_heatmap_example().submit_tracked_frame(
        fake, 7, [("A", 10, 20), ("B", 30, 40)], 1234.25, "test",
    )

    assert [kind for kind, _ in fake.rows] == ["detection", "detection", "frame"]
    assert [payload["ts"] for _, payload in fake.rows] == [1234.25] * 3
    assert len({payload["sample_id"] for _, payload in fake.rows}) == 1
    assert fake.rows[-1][1]["count"] == 2


def test_example_submits_zero_frame_marker_when_no_tracks_exist():
    fake = FakeStoreLens()
    load_heatmap_example().submit_tracked_frame(fake, 7, [], 1234.25, "test")

    assert len(fake.rows[0][1]["sample_id"]) > 0
    payload = {key: value for key, value in fake.rows[0][1].items() if key != "sample_id"}
    assert [(fake.rows[0][0], payload)] == [("frame", {
        "source_id": 7,
        "entity_type": "person",
        "count": 0,
        "ts": 1234.25,
        "attributes": {"detector": "test"},
    })]
