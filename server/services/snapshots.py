"""Frame capture for sources. Tries OpenCV (rtsp/http/webcam/file); falls back to a
generated placeholder so the UI always has an image to show."""
import io
import os
import struct
import zlib
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

from ..db import SNAP_DIR, jload

CAPTURE_TIMEOUT_S = 8
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|stimeout;5000000")

_pool = ThreadPoolExecutor(max_workers=2)


def connect_url(source: dict) -> str:
    """Resolve the openable URL/identifier for a source, injecting credentials."""
    kind, url = source.get("kind", "rtsp"), source.get("url") or ""
    user, pwd = source.get("username") or "", source.get("password") or ""
    if kind in ("rtsp", "http") and user and "://" in url and "@" not in url.split("://", 1)[1].split("/", 1)[0]:
        scheme, rest = url.split("://", 1)
        cred = f"{user}:{pwd}@" if pwd else f"{user}@"
        return f"{scheme}://{cred}{rest}"
    return url


def snapshot_path(source_id: int) -> str:
    return os.path.join(SNAP_DIR, f"{source_id}.jpg")


def _grab(source: dict):
    import cv2  # noqa: deferred so the server runs without opencv

    kind = source["kind"]
    if kind == "webcam":
        idx = int(source.get("url") or jload(source.get("extra_json"), {}).get("device", 0) or 0)
        cap = cv2.VideoCapture(idx)
    else:
        cap = cv2.VideoCapture(connect_url(source))
    try:
        ok, frame = cap.read()
        if not ok or frame is None:
            return None
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        return bytes(buf) if ok else None
    finally:
        cap.release()


def capture(source: dict) -> tuple[str, bytes | None]:
    """Returns (status, jpeg_bytes|None). status: online|offline|unsupported."""
    if source["kind"] == "webrtc":
        return "unsupported", None  # WebRTC ingestion happens in a worker, not server-side
    try:
        fut = _pool.submit(_grab, source)
        data = fut.result(timeout=CAPTURE_TIMEOUT_S)
    except (ImportError, FuturesTimeout, Exception):
        data = None
    if data:
        with open(snapshot_path(source["id"]), "wb") as f:
            f.write(data)
        return "online", data
    return "offline", None


def placeholder_png(source: dict, note: str = "no signal") -> bytes:
    """Placeholder image; PIL if available, else a solid pure-stdlib PNG."""
    w, h = 640, 360
    try:
        from PIL import Image, ImageDraw

        img = Image.new("RGB", (w, h), (26, 26, 25))
        d = ImageDraw.Draw(img)
        for y in range(0, h, 4):  # subtle scanline texture
            d.line([(0, y), (w, y)], fill=(30, 30, 29))
        d.rectangle([0, 0, w - 1, h - 1], outline=(56, 56, 53))
        d.line([(16, 16), (40, 16), (16, 16), (16, 40)], fill=(137, 135, 129), width=2)
        d.line([(w - 40, h - 16), (w - 16, h - 16), (w - 16, h - 16), (w - 16, h - 40)], fill=(137, 135, 129), width=2)
        d.text((24, h // 2 - 20), source.get("name", "camera"), fill=(195, 194, 183))
        d.text((24, h // 2 + 2), f"[ {note} ]", fill=(137, 135, 129))
        d.text((24, h // 2 + 24), f"{source.get('kind', '?')}://{(source.get('url') or '')[:52]}", fill=(90, 89, 84))
        out = io.BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()
    except ImportError:
        return _solid_png(w, h, (26, 26, 25))


def _solid_png(w: int, h: int, rgb: tuple[int, int, int]) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    row = b"\x00" + bytes(rgb) * w
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row * h))
        + chunk(b"IEND", b"")
    )


def get_snapshot_bytes(source: dict) -> tuple[bytes, str]:
    """Latest stored frame, else a placeholder. Returns (bytes, media_type)."""
    p = snapshot_path(source["id"])
    if os.path.exists(p):
        with open(p, "rb") as f:
            data = f.read()
        return data, "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
    data = placeholder_png(source, "no snapshot yet — click refresh" if source["kind"] != "webrtc" else "webrtc: frames come from workers")
    return data, "image/png"
