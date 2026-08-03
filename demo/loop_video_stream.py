r"""Loop one local video as a browser- and OpenCV-readable MJPEG camera.

Usage:
    python demo/loop_video_stream.py --video C:\path\to\video.mp4

Viewer:   http://127.0.0.1:8765/stream.mjpg
Snapshot: http://127.0.0.1:8765/snapshot.jpg
"""
from __future__ import annotations

import argparse
import html
import os
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock, Thread

import cv2


class LoopingVideo:
    def __init__(self, path: str, fps_limit: float):
        self.path = path
        self.fps_limit = fps_limit
        self.jpeg: bytes | None = None
        self.lock = Lock()
        self.running = True
        self.thread = Thread(target=self._read_forever, daemon=True)
        self.thread.start()

    def _read_forever(self) -> None:
        while self.running:
            capture = cv2.VideoCapture(self.path)
            if not capture.isOpened():
                print(f"Could not open {self.path}", flush=True)
                time.sleep(1)
                continue
            source_fps = capture.get(cv2.CAP_PROP_FPS) or self.fps_limit
            delay = 1.0 / max(1.0, min(source_fps, self.fps_limit))
            while self.running:
                started = time.monotonic()
                ok, frame = capture.read()
                if not ok:
                    break
                encoded, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 84])
                if encoded:
                    with self.lock:
                        self.jpeg = jpeg.tobytes()
                time.sleep(max(0.0, delay - (time.monotonic() - started)))
            capture.release()

    def frame(self) -> bytes | None:
        with self.lock:
            return self.jpeg


def serve(video_path: str, host: str, port: int, fps: float) -> None:
    stream = LoopingVideo(video_path, fps)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path in {"/", "/index.html"}:
                body = (
                    "<h1>StoreLens demo camera</h1>"
                    f"<p>{html.escape(os.path.basename(video_path))}</p>"
                    '<p><a href="/stream.mjpg">Live stream</a> · '
                    '<a href="/snapshot.jpg">Calibration snapshot</a></p>'
                    '<img src="/stream.mjpg" style="max-width:100%">'
                ).encode()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/snapshot.jpg":
                frame = stream.frame()
                if frame is None:
                    self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "No frame available yet")
                    return
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(frame)))
                self.end_headers()
                self.wfile.write(frame)
                return
            if self.path != "/stream.mjpg":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                while True:
                    frame = stream.frame()
                    if frame is None:
                        time.sleep(0.05)
                        continue
                    self.wfile.write(
                        b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                        + str(len(frame)).encode()
                        + b"\r\n\r\n"
                        + frame
                        + b"\r\n"
                    )
                    time.sleep(1.0 / fps)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, message: str, *args) -> None:
            print(f"[viewer] {self.address_string()} {message % args}", flush=True)

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Looping:  {video_path}", flush=True)
    print(f"Viewer:   http://{host}:{port}/", flush=True)
    print(f"Stream:   http://{host}:{port}/stream.mjpg", flush=True)
    print(f"Snapshot: http://{host}:{port}/snapshot.jpg", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stream.running = False
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Loop a video as a local StoreLens demo camera.")
    parser.add_argument("--video", required=True, help="Path to a video file")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--fps", type=float, default=20.0)
    arguments = parser.parse_args()
    if not os.path.isfile(arguments.video):
        raise SystemExit(f"Video does not exist: {arguments.video}")
    serve(os.path.abspath(arguments.video), arguments.host, arguments.port, arguments.fps)
