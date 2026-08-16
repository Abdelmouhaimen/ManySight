"""Export the guided demo as a static bundle a plain web host can serve.

The public ManySight landing page has no backend. It shows the same four-camera
demo the product ships, but as immutable files: the videos, one manifest and one
replay artifact. This script produces those files.

Everything it writes was computed by ManySight itself. The timeline, the
combined positions, the occupancy result and its quality, and the alert events
all come from the committed derived replay cache, which was generated offline by
running the real fixture through the real pipeline. Nothing here recomputes any
of it, and the landing that consumes the output must not either.

The cache is validated with the platform's own rules before anything is written.
A cache ManySight considers stale is not something to publish, so a mismatch is
a hard failure rather than a warning.

    python demo/export_static_landing_demo.py --output ../landing/public/demo

Run it from a checkout that has a valid demo cache and the source dataset
installed; the output directory can live anywhere, including an ignored
standalone landing project.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BUNDLE_VERSION = 1

# Keys that must never reach a public bundle, matched case-insensitively against
# every key in the exported payload.
FORBIDDEN_KEY_PARTS = (
    "password", "secret", "credential", "api_key", "apikey", "token",
    "workspace_path", "database", "db_path", "connection", "locator", "url",
    "path", "username", "user_name", "host", "env",
)


class ExportError(RuntimeError):
    """A problem that must stop the export rather than degrade the bundle."""


# --------------------------------------------------------------------- inputs

def load_sources():
    """Load the recipe, the raw fixture and the validated derived cache.

    Validation is the platform's own: `load_derived_cache` re-derives every
    provenance hash and refuses a cache that no longer matches the code, the
    recipe or the fixture that produced it.
    """
    from fastapi import HTTPException

    from server.services import demo_runtime

    # The cache loader memoises; an export run should read what is on disk now.
    for loader in (demo_runtime.load_derived_cache, demo_runtime.load_recipe):
        if hasattr(loader, "cache_clear"):
            loader.cache_clear()

    try:
        recipe = demo_runtime.load_recipe()
        fixture_metadata, fixture_records = demo_runtime.load_fixture()
        cache = demo_runtime.load_derived_cache()
    except HTTPException as exc:
        raise ExportError(
            f"{exc.detail}\n\n"
            "The public bundle must come from a replay cache ManySight considers "
            "valid. Rebuild it on a machine that has the source dataset:\n"
            "    python demo/build_mv3dt_demo_fixture.py\n"
            "then re-run this export."
        ) from exc
    return recipe, fixture_metadata, fixture_records, cache


# -------------------------------------------------------------------- payload

def camera_entries(recipe: dict) -> list[dict]:
    """One public entry per camera: what to play and what to draw on it."""
    frame = recipe["frame"]
    zone = recipe["zone"]
    entries = []
    for index, camera in enumerate(recipe["cameras"], start=1):
        polygons = camera.get("zone_view_polygons_px") or (
            [camera["zone_view_px"]] if camera.get("zone_view_px") else []
        )
        entries.append({
            "id": camera["key"],
            "name": f"Camera {index}",
            "video": f"./camera-{index}.mp4",
            "width": frame["width"],
            "height": frame["height"],
            # The camera's own view of the aisle, in its pixels. Drawn as-is.
            "zones": [{
                "name": zone["name"],
                "color": zone["color"],
                "polygons_px": polygons,
            }] if polygons else [],
        })
    return entries


def zone_polygon_m(cache: dict) -> list[list[float]]:
    """The canonical aisle footprint in map metres, for the floor preview."""
    geometry = (cache.get("geometry") or {}).get("canonical_geometry") or {}
    kind = geometry.get("type")
    if kind == "Polygon":
        return geometry.get("coordinates", [[]])[0]
    if kind == "MultiPolygon":
        # The landing draws one outline; the demo zone is a single footprint.
        return geometry.get("coordinates", [[[[]]]])[0][0]
    raise ExportError(f"unsupported canonical zone geometry: {kind!r}")


def build_manifest(recipe: dict, fixture_metadata: dict, cache: dict) -> dict:
    """Everything needed to bootstrap playback, and nothing else."""
    metadata = cache["metadata"]
    alert = recipe["alert"]
    return {
        "version": BUNDLE_VERSION,
        "duration_s": metadata["duration_s"],
        "fps": metadata["source_fps"],
        "frame_count": fixture_metadata["frame_count"],
        "sample_rate_hz": metadata["sample_rate_hz"],
        "cameras": camera_entries(recipe),
        "replay": "./replay.json",
        "plan": {
            "image": "./plan.png",
            "width_m": recipe["store"]["width_m"],
            "height_m": recipe["store"]["height_m"],
        },
        "zone": {"name": recipe["zone"]["name"], "polygon_m": zone_polygon_m(cache)},
        # The question the demo answers, and the rule that watches it. Exported
        # so the page's wording cannot drift from the rule that actually fired:
        # ">=" must read "at least", ">" must read "more than".
        "question": recipe["query"]["name"],
        "alert_rule": {
            "operator": alert["operator"],
            "value": alert["value"],
            "phrase": phrase_for(alert["operator"], alert["value"], recipe["zone"]["name"]),
        },
        # Provenance, so a bundle can be traced back to what produced it.
        "provenance": {
            "recipe_version": metadata["recipe_version"],
            "fixture_version": metadata["fixture_version"],
            "payload_sha256": metadata["payload_sha256"],
            "derivation_code_hash": metadata["derivation_code_hash"],
            "generated_at": metadata["generated_at"],
            "dataset": fixture_metadata["dataset"],
            "detector": fixture_metadata["producer"]["detector"],
            "tracker": fixture_metadata["producer"]["tracker"],
        },
    }


OPERATOR_WORDS = {
    ">": "More than", ">=": "At least", "<": "Fewer than",
    "<=": "At most", "==": "Exactly",
}


def phrase_for(operator: str, value, zone_name: str) -> str:
    word = OPERATOR_WORDS.get(operator)
    if word is None:
        raise ExportError(f"no public wording for alert operator {operator!r}")
    return f"{word} {value} people in {zone_name}"


def build_replay(cache: dict, fixture_records: list[dict], recipe: dict) -> dict:
    """The derived timeline plus the per-frame camera evidence, trimmed.

    Only the fields the landing draws survive. Everything is copied verbatim
    from what ManySight produced — no value is recomputed, rounded or inferred.
    """
    timeline = []
    for sample in cache["timeline"]:
        kpi = sample.get("kpi") or {}
        timeline.append({
            "index": sample["index"],
            "video_time_s": sample["video_time_s"],
            # The answer and ManySight's confidence in it, as derived.
            "kpi": {
                "value": kpi.get("value"),
                "quality": kpi.get("quality"),
                "source_count": (kpi.get("evidence") or {}).get("source_count"),
            } if kpi else None,
            # Combined people: where they are, and which camera track each came
            # from, so a box can take its person's colour.
            "entities": [{
                "id": entity["fused_entity_id"],
                "point_map": entity.get("point_map"),
                "members": [{
                    "camera": member.get("source_key"),
                    "local_track_id": member.get("local_entity_id"),
                } for member in entity.get("members", [])],
            } for entity in sample.get("fused_entities", [])],
            # Alerts ManySight actually recorded at this moment.
            "alerts": [{
                "name": event.get("name"),
                "video_time_s": event.get("video_time_s", sample["video_time_s"]),
            } for event in sample.get("alert_events", [])],
        })

    allowed_cameras = {camera["key"] for camera in recipe["cameras"]}
    camera_frames: dict[str, list[dict]] = {key: [] for key in sorted(allowed_cameras)}
    for record in fixture_records:
        key = record.get("source_key")
        if key not in allowed_cameras:
            continue
        camera_frames[key].append({
            "frame_index": record["frame_index"],
            "detections": [{
                "bbox_px": detection["bbox_px"],
                "point_px": detection.get("point_px"),
                "local_track_id": detection.get("local_track_id"),
                "confidence": detection.get("confidence"),
            } for detection in record.get("detections", [])],
        })
    for key in camera_frames:
        camera_frames[key].sort(key=lambda frame: frame["frame_index"])

    return {
        "version": BUNDLE_VERSION,
        "sample_rate_hz": cache["metadata"]["sample_rate_hz"],
        "timeline": timeline,
        "camera_frames": camera_frames,
    }


# --------------------------------------------------------------------- checks

def audit(payload, path: str = "$") -> list[str]:
    """Find anything in the bundle that has no business being published."""
    problems = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            lowered = str(key).lower()
            if any(part in lowered for part in FORBIDDEN_KEY_PARTS):
                problems.append(f"{path}.{key}: forbidden key")
            problems.extend(audit(value, f"{path}.{key}"))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            problems.extend(audit(value, f"{path}[{index}]"))
    elif isinstance(payload, str):
        looks_like_path = payload.startswith(("/", "~/", "C:\\", "\\\\")) or "://" in payload
        if looks_like_path:
            problems.append(f"{path}: looks like a path or URL ({payload[:60]!r})")
    return problems


def check_bundle(manifest: dict, replay: dict) -> None:
    problems = audit(manifest, "manifest") + audit(replay, "replay")
    if problems:
        raise ExportError("the bundle would leak private data:\n  " + "\n  ".join(problems))

    referenced = {camera["video"] for camera in manifest["cameras"]}
    if len(referenced) != len(manifest["cameras"]):
        raise ExportError("two cameras reference the same video file")
    for camera in manifest["cameras"]:
        if camera["id"] not in replay["camera_frames"]:
            raise ExportError(f"no exported frames for camera {camera['id']}")
    if not replay["timeline"]:
        raise ExportError("the exported replay has no derived samples")


# ---------------------------------------------------------------------- write

def write_json(path: Path, payload: dict) -> int:
    """Deterministic output: same input, byte-identical file."""
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8")
    return len(text) + 1


def copy_media(recipe: dict, output: Path, asset_root: Path) -> list[tuple[str, int]]:
    """Copy the four camera videos and the bird's-eye plan into the bundle."""
    copied = []
    videos = asset_root / "videos"
    for index, camera in enumerate(recipe["cameras"], start=1):
        source = videos / f"{camera['key']}.mp4"
        if not source.is_file():
            raise ExportError(f"missing demo video: {source}")
        target = output / f"camera-{index}.mp4"
        shutil.copyfile(source, target)
        copied.append((target.name, target.stat().st_size))
    plan = asset_root / "map.png"
    if not plan.is_file():
        raise ExportError(f"missing bird's-eye plan: {plan}")
    shutil.copyfile(plan, output / "plan.png")
    copied.append(("plan.png", (output / "plan.png").stat().st_size))
    return copied


def export(output: Path, asset_root: Path | None, skip_media: bool) -> dict:
    recipe, fixture_metadata, fixture_records, cache = load_sources()

    manifest = build_manifest(recipe, fixture_metadata, cache)
    replay = build_replay(cache, fixture_records, recipe)
    check_bundle(manifest, replay)

    output.mkdir(parents=True, exist_ok=True)
    written = [
        ("manifest.json", write_json(output / "manifest.json", manifest)),
        ("replay.json", write_json(output / "replay.json", replay)),
    ]

    if skip_media:
        print("media: skipped (--skip-media)")
    else:
        from server.services import demo_runtime
        root = Path(asset_root).expanduser().resolve() if asset_root else demo_runtime.resolve_asset_root()
        if root is None:
            raise ExportError(
                "the source dataset is not installed, so the videos cannot be exported.\n"
                "Install it with `python demo/fetch_nvidia_mv3dt.py`, point "
                "MANYSIGHT_DEMO_ASSET_DIR at it, or pass --assets PATH.\n"
                "Use --skip-media to write only manifest.json and replay.json."
            )
        written.extend(copy_media(recipe, output, root))

    return {"output": output, "written": written, "samples": len(replay["timeline"])}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--output", required=True, type=Path,
                        help="directory to write the bundle into, e.g. ../landing/public/demo")
    parser.add_argument("--assets", type=Path, default=None,
                        help="source dataset root; defaults to the usual demo asset locations")
    parser.add_argument("--skip-media", action="store_true",
                        help="write only manifest.json and replay.json")
    args = parser.parse_args(argv)

    try:
        result = export(args.output.expanduser().resolve(), args.assets, args.skip_media)
    except ExportError as error:
        print(f"export failed: {error}", file=sys.stderr)
        return 1

    print(f"exported {result['samples']} derived samples to {result['output']}")
    total = 0
    for name, size in result["written"]:
        total += size
        print(f"  {name:>16}  {size / 1_000_000:8.2f} MB" if size > 1_000_000
              else f"  {name:>16}  {size / 1000:8.1f} kB")
    print(f"  {'total':>16}  {total / 1_000_000:8.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
