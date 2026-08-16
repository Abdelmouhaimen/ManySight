"""Representative continuous detection-worker frame semantics.

The shipped examples are the files an agent is most likely to copy, so they must
demonstrate the current contract rather than a superseded one: one atomic
envelope per processed frame, an empty frame submitted as a real observed zero,
and one exact timestamp shared by the whole sample.
"""

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_heatmap_example():
    spec = importlib.util.spec_from_file_location(
        "manysight_heatmap_example",
        ROOT / "examples" / "heatmap_tracker.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeSample:
    def __init__(self, client, source_id, entity_type, ts, attributes):
        self.client = client
        self.payload = {"source_id": source_id, "entity_type": entity_type, "ts": ts,
                        "attributes": attributes, "detections": []}

    def add_detection(self, **track):
        self.payload["detections"].append(track)
        return self

    def submit(self):
        self.client.samples.append(self.payload)
        return {"status": "accepted"}


class FakeManySight:
    """Only the sample builder — using anything else would be the old contract."""

    def __init__(self):
        self.samples = []

    def begin_detection_sample(self, source_id, entity_type, ts=None, attributes=None):
        return FakeSample(self, source_id, entity_type, ts, attributes)


def test_example_submits_one_atomic_sample_per_processed_frame():
    fake = FakeManySight()
    load_heatmap_example().submit_tracked_frame(
        fake, 7, [("A", 10, 20), ("B", 30, 40)], 1234.25, "test",
    )

    assert len(fake.samples) == 1, "one frame is one envelope, not a row per detection"
    sample = fake.samples[0]
    assert sample["source_id"] == 7 and sample["entity_type"] == "person"
    assert sample["ts"] == 1234.25, "one exact timestamp for the whole frame"
    assert sample["attributes"] == {"detector": "test"}
    assert [d["entity_id"] for d in sample["detections"]] == ["A", "B"]
    assert [d["point_px"] for d in sample["detections"]] == [(10, 20), (30, 40)]
    assert {d["identity_scope"] for d in sample["detections"]} == {"source"}


def test_example_submits_an_empty_frame_as_a_real_observed_zero():
    fake = FakeManySight()
    load_heatmap_example().submit_tracked_frame(fake, 7, [], 1234.25, "test")

    assert len(fake.samples) == 1, "an empty processed frame is still submitted"
    assert fake.samples[0]["detections"] == [], "never a fake detection standing in for zero"


def test_the_example_tracker_is_not_capped_by_a_hard_coded_sleep():
    """The rate mistake this milestone exists to prevent, pinned in the examples."""
    for name in ("heatmap_tracker.py", "dwell_zones.py"):
        source = (ROOT / "examples" / name).read_text(encoding="utf-8")
        assert "time.sleep" not in source, f"{name} paces its capture loop with a sleep"
        assert "SubmissionGate" in source, f"{name} must gate submission, not slow the tracker"
        assert "processing_fps" in source, f"{name} must report the rate it achieved"
