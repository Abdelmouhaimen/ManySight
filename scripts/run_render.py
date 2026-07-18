"""Run the fresh StoreLens demo behind Render's single public HTTP port."""
import os
import signal
import subprocess
import sys
import time


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def terminate(processes):
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


def main():
    os.chdir(ROOT)
    shared = os.environ.copy()
    port = int(shared.get("PORT", "10000"))
    public_url = (
        shared.get("STORELENS_PUBLIC_URL")
        or shared.get("RENDER_EXTERNAL_URL")
        or f"http://localhost:{port}"
    ).rstrip("/")
    shared.update({
        "STORELENS_ENDPOINT_PROFILE": "render",
        "STORELENS_PUBLIC_URL": public_url,
        "STORELENS_PUBLIC_MCP_URL": f"{public_url}/mcp",
        "STORELENS_PUBLIC_READS": "true",
        "STORELENS_SEED_DEMO": "false",
    })

    api_env = shared.copy()
    api_env["PORT"] = "8080"
    api = subprocess.Popen([
        sys.executable, "-m", "uvicorn", "server.app:app",
        "--host", "0.0.0.0", "--port", "8080",
        "--proxy-headers", "--forwarded-allow-ips", "*",
    ], env=api_env)

    mcp_env = shared.copy()
    mcp_env.update({
        "STORELENS_URL": "http://127.0.0.1:8080",
        "STORELENS_MCP_TRANSPORT": "streamable-http",
        "STORELENS_MCP_HOST": "127.0.0.1",
        "STORELENS_MCP_PORT": "8001",
        "STORELENS_MCP_DNS_REBINDING_PROTECTION": "false",
    })
    mcp = subprocess.Popen([sys.executable, "mcp_server/server.py"], env=mcp_env)

    template_path = os.path.join(ROOT, "deploy", "render", "nginx.conf.template")
    nginx_path = "/tmp/storelens-nginx.conf"
    with open(template_path, encoding="utf-8") as handle:
        config = handle.read().replace("__PORT__", str(port))
    with open(nginx_path, "w", encoding="utf-8") as handle:
        handle.write(config)
    for path in (
        "/tmp/storelens-client-body",
        "/tmp/storelens-proxy",
        "/tmp/storelens-fastcgi",
        "/tmp/storelens-uwsgi",
        "/tmp/storelens-scgi",
    ):
        os.makedirs(path, exist_ok=True)
    nginx = subprocess.Popen(["nginx", "-g", "daemon off;", "-c", nginx_path], env=shared)
    processes = [api, mcp, nginx]

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
