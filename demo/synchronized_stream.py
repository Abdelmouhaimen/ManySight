"""Tightly scoped four-camera loop service used only after demo promotion."""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2


class Shared:
    def __init__(self, videos: list[Path]):
        self.condition = threading.Condition()
        self.videos = videos
        self.jpegs: list[bytes] = []
        self.frame_index = -1
        self.loop_index = 0
        self.updated_at = None
        self.fps = 0.0
        self.stopped = False


def produce(shared: Shared) -> None:
    captures = [cv2.VideoCapture(str(path)) for path in shared.videos]
    if not all(capture.isOpened() for capture in captures):
        raise RuntimeError("one or more controlled demo videos could not be opened")
    shared.fps = min(float(capture.get(cv2.CAP_PROP_FPS) or 30) for capture in captures)
    deadline = time.monotonic()
    try:
        while not shared.stopped:
            decoded = [capture.read() for capture in captures]
            if not all(ok for ok, _ in decoded):
                for capture in captures:
                    capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                decoded = [capture.read() for capture in captures]
                if not all(ok for ok, _ in decoded):
                    raise RuntimeError("synchronized rewind failed")
                shared.loop_index += 1
                shared.frame_index = -1
            encoded = []
            for _, frame in decoded:
                ok, jpeg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
                if not ok:
                    raise RuntimeError("JPEG encoding failed")
                encoded.append(jpeg.tobytes())
            with shared.condition:
                shared.jpegs = encoded; shared.frame_index += 1; shared.updated_at = time.time()
                shared.condition.notify_all()
            deadline += 1 / shared.fps
            time.sleep(max(0, deadline - time.monotonic()))
            if deadline < time.monotonic():
                deadline = time.monotonic()
    finally:
        for capture in captures:
            capture.release()


def handler(shared: Shared):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/status.json":
                body = json.dumps({"camera_count": 4, "fps": shared.fps,
                                   "frame_index": shared.frame_index,
                                   "loop_index": shared.loop_index,
                                   "updated_at": shared.updated_at}).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
                return
            parts = [part for part in path.split("/") if part]
            if len(parts) != 2 or not parts[0].startswith("cam_"):
                self.send_error(404); return
            try:
                index = int(parts[0][4:])
            except ValueError:
                self.send_error(404); return
            if index not in range(4) or parts[1] not in {"snapshot.jpg", "stream.mjpg"}:
                self.send_error(404); return
            if parts[1] == "snapshot.jpg":
                with shared.condition:
                    if not shared.jpegs: shared.condition.wait(timeout=3)
                    if not shared.jpegs: self.send_error(503); return
                    jpeg = shared.jpegs[index]
                self.send_response(200); self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(jpeg))); self.end_headers(); self.wfile.write(jpeg)
                return
            self.send_response(200); self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame"); self.end_headers()
            marker = None
            try:
                while True:
                    with shared.condition:
                        current = (shared.loop_index, shared.frame_index)
                        if current == marker: shared.condition.wait(timeout=2)
                        current = (shared.loop_index, shared.frame_index)
                        if not shared.jpegs or current == marker: continue
                        jpeg = shared.jpegs[index]
                    marker = current
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n")
                    self.wfile.write(f"Content-Length: {len(jpeg)}\r\n\r\n".encode())
                    self.wfile.write(jpeg + b"\r\n")
            except (BrokenPipeError, ConnectionResetError):
                return
    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset-root", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    root = args.asset_root.resolve()
    videos = [root / "videos" / f"Warehouse_Synthetic_Cam{i:03d}.mp4" for i in range(1, 5)]
    if not all(path.is_file() and root in path.resolve().parents for path in videos):
        raise SystemExit("the controlled four-camera asset set is incomplete")
    shared = Shared(videos)
    producer = threading.Thread(target=produce, args=(shared,), daemon=True)
    producer.start()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(shared))
    try:
        server.serve_forever()
    finally:
        shared.stopped = True
        with shared.condition: shared.condition.notify_all()
        server.server_close(); producer.join(timeout=3)


if __name__ == "__main__":
    main()
