# Guided demo runtime media

This directory is the complete runtime media bundle for **Try Demo**:

- `map.png` — bird's-eye warehouse plan;
- `videos/Warehouse_Synthetic_Cam001.mp4` through `Cam004.mp4` — the four
  synchronized camera recordings used by the committed fixture and replay.

It intentionally excludes the other eight `mtmc_12cam` videos, camera metadata, and
the full source archive. Those are maintainer-only inputs for regenerating the raw
fixture or validating the derived replay.

The media originates from NVIDIA DeepStream's MV3DT synthetic warehouse sample:
<https://github.com/NVIDIA/DeepStream/tree/main/src/apps/reference_apps/deepstream-tracker-3d-multi-view/assets>.
It is redistributed here with the project maintainer's confirmed permission. The
ManySight Apache-2.0 license does not relicense this third-party media.
