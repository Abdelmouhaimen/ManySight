"""Run StoreLens API and MCP together and checkpoint SQLite to object storage."""
import gzip
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

import requests


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("STORELENS_DATA", os.path.join(ROOT, "data"))
DB_PATH = os.path.join(DATA_DIR, "storelens.db")
STATE_URL = os.environ.get("STORELENS_STATE_URL", "")
BACKUP_INTERVAL_S = max(2.0, float(os.environ.get("STORELENS_BACKUP_INTERVAL_S", "5")))
STOP = threading.Event()


def restore_state():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not STATE_URL or os.path.exists(DB_PATH):
        return
    response = requests.get(STATE_URL, timeout=30)
    if response.status_code == 404:
        return
    response.raise_for_status()
    fd, temporary = tempfile.mkstemp(prefix="storelens-restore-", suffix=".db", dir=DATA_DIR)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(response.content)
        os.replace(temporary, DB_PATH)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def backup_state():
    if not STATE_URL or not os.path.exists(DB_PATH):
        return
    fd, temporary = tempfile.mkstemp(prefix="storelens-backup-", suffix=".db")
    os.close(fd)
    try:
        source = sqlite3.connect(DB_PATH, timeout=30)
        target = sqlite3.connect(temporary)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        with open(temporary, "rb") as handle:
            payload = gzip.compress(handle.read(), compresslevel=6)
            response = requests.put(
                STATE_URL,
                data=payload,
                headers={
                    "Content-Type": "application/vnd.sqlite3",
                    "Content-Encoding": "gzip",
                },
                timeout=60,
            )
        response.raise_for_status()
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def backup_loop():
    while not STOP.wait(BACKUP_INTERVAL_S):
        try:
            backup_state()
        except Exception as exc:
            print(f"StoreLens state checkpoint failed: {exc}", file=sys.stderr, flush=True)


def seed_if_requested():
    if os.path.exists(DB_PATH):
        return
    if os.environ.get("STORELENS_SEED_DEMO", "false").lower() not in {"1", "true", "yes"}:
        return
    subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "seed_demo.py")], check=True)


def terminate(processes):
    STOP.set()
    for process in processes:
        if process.poll() is None:
            process.terminate()
    deadline = time.time() + 10
    for process in processes:
        if process.poll() is None:
            try:
                process.wait(max(0.1, deadline - time.time()))
            except subprocess.TimeoutExpired:
                process.kill()
    try:
        backup_state()
    except Exception as exc:
        print(f"Final StoreLens state checkpoint failed: {exc}", file=sys.stderr, flush=True)


def main():
    os.chdir(ROOT)
    restore_state()
    seed_if_requested()
    shared = os.environ.copy()
    api_port = shared.get("PORT", "8080")
    api = subprocess.Popen([
        sys.executable, "-m", "uvicorn", "server.app:app",
        "--host", "0.0.0.0", "--port", api_port,
        "--proxy-headers", "--forwarded-allow-ips", "*",
    ], env=shared)
    mcp_env = shared.copy()
    mcp_env.update({
        "STORELENS_URL": f"http://127.0.0.1:{api_port}",
        "STORELENS_MCP_TRANSPORT": "streamable-http",
        "STORELENS_MCP_HOST": "0.0.0.0",
        "STORELENS_MCP_PORT": "8001",
        "STORELENS_MCP_DNS_REBINDING_PROTECTION": "false",
    })
    mcp = subprocess.Popen([sys.executable, "mcp_server/server.py"], env=mcp_env)
    processes = [api, mcp]
    thread = threading.Thread(target=backup_loop, daemon=True)
    thread.start()

    def stop_handler(_signum, _frame):
        terminate(processes)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    while True:
        for process in processes:
            code = process.poll()
            if code is not None:
                terminate(processes)
                raise SystemExit(code)
        time.sleep(0.5)


if __name__ == "__main__":
    main()
