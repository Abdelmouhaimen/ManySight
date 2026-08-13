"""Supervisor for the one known synchronized demo-media process."""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

_process: subprocess.Popen | None = None
_asset_root: Path | None = None


def start(asset_root: str) -> str:
    global _process, _asset_root
    root = Path(asset_root).resolve()
    if _process and _process.poll() is None and _asset_root == root:
        return f"http://127.0.0.1:{_port()}"
    stop()
    port = _port()
    script = Path(__file__).resolve().parents[2] / "demo" / "synchronized_stream.py"
    kwargs = {"cwd": str(script.parents[1]), "stdin": subprocess.DEVNULL,
              "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    _process = subprocess.Popen(
        [sys.executable, str(script), "--asset-root", str(root), "--port", str(port)], **kwargs)
    _asset_root = root
    deadline = time.monotonic() + 12
    status_url = f"http://127.0.0.1:{port}/status.json"
    while time.monotonic() < deadline:
        if _process.poll() is not None:
            break
        try:
            with urllib.request.urlopen(status_url, timeout=0.5) as response:
                if response.status == 200:
                    return f"http://127.0.0.1:{port}"
        except Exception:
            time.sleep(0.1)
    stop()
    raise RuntimeError("the synchronized demo stream supervisor did not become ready")


def status() -> dict:
    return {"running": bool(_process and _process.poll() is None),
            "pid": _process.pid if _process and _process.poll() is None else None,
            "port": _port(), "asset_root_configured": _asset_root is not None}


def stop() -> None:
    global _process, _asset_root
    if _process and _process.poll() is None:
        _process.terminate()
        try:
            _process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _process.kill(); _process.wait(timeout=2)
    _process = None; _asset_root = None


def _port() -> int:
    return int(os.environ.get("STORELENS_DEMO_STREAM_PORT", "8765"))
