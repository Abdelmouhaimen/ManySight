"""Plain helper functions for building request bodies in tests. Not a fixture
module — imported directly (`from helpers import make_detection`), which works
because pytest adds `tests/` to sys.path for rootless test packages."""


def make_detection(source_id, observation_id, ts, entity_id="e1", point_px=None, **extra):
    body = {
        "schema_version": 2, "observation_id": observation_id, "kind": "detection",
        "timestamp": ts, "source_id": source_id, "entity_id": entity_id,
        "entity_type": "person", "label": "customer",
    }
    if point_px is not None:
        body["geometry"] = {"point_px": list(point_px)}
    body.update(extra)
    return body


def make_state(source_id, observation_id, ts, label, name="door_state", entity_id=None, **extra):
    body = {
        "schema_version": 2, "observation_id": observation_id, "kind": "state",
        "timestamp": ts, "source_id": source_id, "name": name, "label": label,
    }
    if entity_id is not None:
        body["entity_id"] = entity_id
    body.update(extra)
    return body


def make_measurement(source_id, observation_id, ts, name, value, value_kind="gauge", **extra):
    body = {
        "schema_version": 2, "observation_id": observation_id, "kind": "measurement",
        "timestamp": ts, "source_id": source_id, "name": name, "value": value,
        "value_kind": value_kind,
    }
    body.update(extra)
    return body
