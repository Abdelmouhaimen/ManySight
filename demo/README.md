# StoreLens hallway demo files

## Projects

- StoreLens dashboard/API: `C:\Users\abdo-\Desktop\projects\storelens`
- Reconstruction experiment (not required for this manual workflow):
  `C:\Users\abdo-\Desktop\projects\video_floorplan_localizer`

Plan tracing is built into StoreLens under **Setup → Space & zones → Digitize plan**.
The source image stays in the browser; only traced metric geometry is saved.

## Demo video camera

The loop helper is `demo\loop_video_stream.py`. It needs OpenCV, which is already a
StoreLens optional dependency.

```powershell
python demo\loop_video_stream.py --video "C:\path\to\camera-video.mp4"
```

It exposes:

- viewer: `http://127.0.0.1:8765/`
- MJPEG camera: `http://127.0.0.1:8765/stream.mjpg`
- still for calibration: `http://127.0.0.1:8765/snapshot.jpg`

To run the zero-capable people worker at two exact-timestamp samples per second:

```powershell
$env:STORELENS_SOURCE_CONNECTION = "http://127.0.0.1:8765/stream.mjpg"
python examples/heatmap_tracker.py --source 3 --fps 2
```

The worker publishes one `detection_frame_count` sample per inference cycle,
including zero-person frames. StoreLens plots that count at its exact timestamp;
neighboring samples are not bucketed or accumulated.

In StoreLens, create an HTTP source whose local reference is
`STORELENS_DEMO_STREAM_0`. A local worker resolves that reference to the MJPEG URL;
the URL itself is not stored by the platform. Paste the MJPEG URL into the source's
browser-preview field to view it directly in the dashboard.

## Manual geometry workflow

1. Open StoreLens → Setup → Space & zones → Digitize plan.
2. Choose the plan image, trace the walkable polygon, mark a known distance, and save it.
3. Add or select the camera source and place it on the map.
4. Open the snapshot URL and save the image locally.
5. Click Calibrate camera, upload that still, and match at least four floor points.
6. Compute and save, then use Test projection.
