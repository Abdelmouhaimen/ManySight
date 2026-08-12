---
name: source-onboarding
description: Use when adding cameras, files, streams, or sensors and configuring managed or external-secret access without exposing credentials.
---

# Source onboarding

1. Inspect `list_sources` and reuse the logical source when appropriate.
2. Choose `storelens_managed` for structured protected connection configuration or
   `external_secret` for a worker-local secret reference.
3. Never place credentials in locator metadata, observations, jobs, dashboards, logs, or
   generated code. Normal discovery must remain redacted.
4. Resolve managed access only in the authorized local worker and pass it directly to
   capture code in memory.
5. Confirm reachability from the worker machine. StoreLens does not proxy or test the feed.
6. Place/calibrate the source and create a multiview group only if shared world geometry is
   actually available.
