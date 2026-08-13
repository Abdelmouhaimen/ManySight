# Local looped-video demo

For the isolated four-camera product walkthrough, use **Try Demo** in ManySight and read
[`docs/guided-demo.md`](../docs/guided-demo.md). The utility below is a separate
single-video development helper.

`loop_video_stream.py` is a development utility that exposes a local video as an MJPEG
stream. It is not a StoreLens service, camera gateway, or production streaming server.

## Start the stream

From the repository root:

```powershell
python demo/loop_video_stream.py --video "C:\path\to\video.mp4"
```

Defaults:

- viewer: <http://127.0.0.1:8765/>
- MJPEG stream: <http://127.0.0.1:8765/stream.mjpg>
- current JPEG frame: <http://127.0.0.1:8765/snapshot.jpg>

Use `--host`, `--port`, `--fps`, or `--no-loop` to change the behavior. The utility
serves frames without authentication; keep it on a trusted development interface.

## Register it as a managed source

In ManySight, open **Sources**, add an HTTP source, choose **StoreLens managed**, and
enter `http://127.0.0.1:8765/stream.mjpg`. A worker on the same machine can then use:

```python
from storelens import StoreLens

client = StoreLens("http://127.0.0.1:8000", api_key="")
source = client.source(source_id)
capture = client.open_capture(source)
```

The worker needs the configured credential-resolution key when the source contains
managed credentials. This unauthenticated demo stream does not.

## External-secret alternative

Choose **External secret** and set the source's local secret reference to a descriptive
environment-variable name such as `STORELENS_DEMO_STREAM_0`. Set that variable only on
the worker machine:

```powershell
$env:STORELENS_DEMO_STREAM_0 = "http://127.0.0.1:8765/stream.mjpg"
```

`client.open_capture(source)` resolves the reference automatically. Use this mode when
connection material should remain outside the StoreLens database.

Source reachability is always relative to the worker machine. Replace loopback with an
address reachable by the worker when the stream and worker run on different hosts.
