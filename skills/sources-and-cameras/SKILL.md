---
name: sources-and-cameras
description: Use when adding or inspecting cameras, files, streams, and sensors — managed vs external-secret access, credential safety, freshness, and how to actually look at a camera view before proposing geometry.
---

# Sources and cameras

Load [`storelens-core`](../storelens-core/SKILL.md) first.

A **source** is a logical observation input. It is not a stream StoreLens holds open:
camera access belongs to the local worker or to your own shell.

## Onboarding

1. `inspect_workspace()` — reuse an existing logical source instead of creating a duplicate.
2. `configure_source(...)` with one of two access models:
   - `storelens_managed`: structured `connection` plus optional `credentials`, encrypted at
     rest and returned only by the privileged connection endpoint.
   - `external_secret`: `locator.local_secret_ref` naming a secret the worker resolves from
     its own environment or keychain.
3. Place the source on the map and calibrate it — or import a rich 3x4 world-to-pixel
   calibration — before any geometry or fusion work.
4. `inspect_source(source_id)` to confirm configuration, placement, calibration, zone
   views, freshness, and observed submission rate.

A camera URL, username, password, or token placed in `locator` is rejected. That is the
guard working, not a bug: move it into a managed `connection` or a local secret reference.

## Credential rules

- `get_source_connection(source_id)` is the only path to connection material. It is
  separately authenticated with `STORELENS_CREDENTIAL_ACCESS_KEY`.
- Call it **only inside the authorized local process that opens the feed**, and pass the
  result straight into capture code in memory.
- Never log, print, display, persist, or echo it — not into observations, zone metadata,
  job metadata, generated code, a commit, or your reply to the user.
- Ordinary reads (`inspect_source`, `inspect_workspace`) are redacted and are what you
  should use for everything else.

## Looking at a camera

Geometry work needs visual evidence. When you are asked to define a physical region, look
at the cameras **before** asking the user for coordinates.

1. `plan_frame_capture(source_id)` returns a runnable local capture plan plus the pixel
   coordinate system and calibration context.
2. Run that plan in your own shell and open the saved image.
3. Propose polygons in those same pixel coordinates.

No API returns live camera pixels. StoreLens does not proxy media, and the MCP adapter
does not process video, so the capture runs in your process — that boundary is deliberate
and must not be worked around.

## Freshness and health

`inspect_source` and `inspect_perception` report the last complete sample, its age, the
observed submission rate, and the latest worker heartbeat.

- A job marked active is not proof a process is alive; check the heartbeat.
- A source that stopped producing goes **stale**. Its last complete sample stays as the
  current scene; staleness is reported separately and never turns into an observed zero.
- Reachability is a property of the worker machine. StoreLens does not test operational
  feeds; its guided demo serves only bundled local sample media.
