"""How fast a local perception worker should run, and on what.

Three rates are routinely conflated, and conflating them produces a tracker that
technically works and silently tracks badly:

* **source FPS** — what the camera or file actually delivers.
* **processing FPS** — how many frames the local detector and tracker consume.
* **submission Hz** — how many complete `DetectionSample` envelopes reach
  ManySight.

They are independent, and only the third is a ManySight concern. A tracker,
however, is a temporal algorithm: association quality degrades with the gap
between consecutive frames, so sparse processing costs identity continuity that
no amount of central derivation can recover. Dwell, visits, flow and multiview
association are all built on that continuity. That is why the processing floor
below is a real requirement and the submission rate is a free, task-driven
choice — submitting every decoded frame is usually pointless, while *processing*
one frame in twenty is usually a defect.

This module is pure policy: no database, no HTTP, no device access. ManySight
runs no models and cannot see the caller's machine, so it recommends rates and
describes the checks; the worker's own process performs them (see
`probe_perception_runtime` in the SDK) and reports what it achieved through
heartbeat metrics.
"""
from __future__ import annotations

# A person tracker below this rate loses association across ordinary walking
# speed: at 5 FPS someone crossing a 4 m aisle moves ~1 m between frames, which
# is wider than the spatial gate multiview fusion uses by default.
TRACKING_MIN_PROCESSING_FPS = 15.0
# Preferred when the source and machine allow it. Above this, tracking quality
# gains little while GPU cost keeps rising, so it is a recommendation ceiling
# rather than a limit — a worker that can sustain source-native rate may use it.
TRACKING_PREFERRED_PROCESSING_FPS = 30.0
# Central cadence for tracked detections. Enough to place visit edges within
# ~0.2 s and to give every default fusion tick a fresh sample, without posting
# every decoded frame.
DEFAULT_TRACKING_SUBMISSION_HZ = 5.0
# A worker with no tracker has no continuity to protect: it only needs to
# process what it submits.
DEFAULT_UNTRACKED_SUBMISSION_HZ = 1.0
# Reported rates within this fraction of target are not a finding; decode jitter
# and heartbeat windowing are both worth a few percent.
TOLERANCE = 0.9

# Ordered by how often each one is the real answer in practice.
LIKELY_CAUSES = [
    "inference is running on CPU while a CUDA device is present but unselected",
    "CUDA is unavailable in the Python environment that actually started the worker",
    "the worker started in the wrong virtualenv or conda environment",
    "the model is too heavy for the device — try a smaller detector variant",
    "inference configuration: oversized input resolution, or FP32 on a device where "
    "FP16 is supported and validated",
    "capture or decode is the bottleneck, not inference — check RTSP transport, stream "
    "resolution, and whether decoding is single-threaded",
]

RATE_DEFINITIONS = {
    "source_fps": "What the camera, stream, or file delivers. A property of the source.",
    "processing_fps": ("How many frames the local detector and tracker actually consume "
                       "per second. This is what tracking quality depends on."),
    "submission_hz": ("How many complete DetectionSample envelopes are posted to ManySight "
                      "per second. Normally lower than processing_fps, and never higher."),
}

HEARTBEAT_METRICS = {
    "source_fps": "measured or declared source rate",
    "processing_fps": "achieved detector+tracker rate, the one that matters for tracking",
    "submission_hz": "achieved central submission rate",
    "device": "cuda | cpu — what inference actually ran on",
    "precision": "fp16 | fp32 — only report fp16 if it was really used",
}


def _round(value: float | None) -> float | None:
    return None if value is None else round(float(value), 2)


def clean_fps(value) -> float | None:
    """A source rate worth believing, or None.

    `cv2.CAP_PROP_FPS` is unreliable: it returns 0 for many network streams and
    occasionally a container timebase such as 90000. A wrong number here would
    silently produce a wrong recommendation, so an implausible one is discarded
    in favour of an honest unknown.
    """
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return None
    if rate != rate or rate <= 0 or rate > 1000:  # NaN, zero, or a container timebase
        return None
    return rate


def rate_plan(source_fps: float | None = None, tracking: bool = True,
              multiview_time_tolerance_s: float | None = None) -> dict:
    """The processing and submission rates to configure, and why.

    Never recommends a rate the source cannot supply, and never silently drops a
    tracking worker to a rate at which tracking stops working: when the source
    itself is slower than the floor, the plan says so instead of pretending the
    floor was met.
    """
    source_fps = clean_fps(source_fps)
    rationale: list[str] = []

    if not tracking:
        submission = DEFAULT_UNTRACKED_SUBMISSION_HZ
        if source_fps is not None:
            submission = min(submission, source_fps)
        rationale.append(
            "No tracker, so there is no frame-to-frame continuity to protect: process only "
            "what you submit and choose the rate from how fast the measured value changes.")
        return {
            "tracking_enabled": False,
            "source_fps": _round(source_fps),
            "minimum_processing_fps": None,
            "target_processing_fps": _round(submission),
            "may_increase_to_fps": _round(source_fps),
            "target_submission_hz": _round(submission),
            "source_limited": False,
            "rationale": rationale,
        }

    if source_fps is None:
        target = TRACKING_MIN_PROCESSING_FPS
        source_limited = False
        rationale.append(
            "The source rate is unknown, so this is the tracking floor, not a tuned target. "
            "Measure the source (cv2.CAP_PROP_FPS, or time a few hundred decoded frames), "
            "then ask for the recipe again with source_fps set.")
    elif source_fps < TRACKING_MIN_PROCESSING_FPS:
        target = source_fps
        source_limited = True
        rationale.append(
            f"The source itself delivers only {source_fps:g} FPS, below the {TRACKING_MIN_PROCESSING_FPS:g} "
            "FPS tracking floor. Process every frame it gives you, and report the limitation "
            "rather than claiming the floor was met — expect weaker association at walking speed.")
    else:
        target = min(source_fps, TRACKING_PREFERRED_PROCESSING_FPS)
        source_limited = False
        rationale.append(
            f"Tracking needs at least {TRACKING_MIN_PROCESSING_FPS:g} FPS; "
            f"{TRACKING_PREFERRED_PROCESSING_FPS:g} FPS is preferred where the hardware "
            "sustains it. Never configure a tracker at 1-5 FPS on a source and machine "
            "capable of substantially more.")
        if source_fps > target:
            rationale.append(
                f"Source-native {source_fps:g} FPS is also correct if the device sustains it; "
                "the gain above the preferred rate is small, so measure before paying for it.")

    submission = min(target, DEFAULT_TRACKING_SUBMISSION_HZ)
    if multiview_time_tolerance_s and multiview_time_tolerance_s > 0:
        needed = 1.0 / float(multiview_time_tolerance_s)
        if needed > submission:
            submission = min(target, needed)
            rationale.append(
                f"Raised to {submission:.2f} Hz so every fusion tick has a sample inside the "
                f"group's {multiview_time_tolerance_s:g}s time tolerance.")
    rationale.append(
        "Submission is a separate rate from processing. Keep the tracker at the processing "
        "target and gate submission — do not sleep the capture loop down to the submission "
        "rate, and do not raise submission to camera FPS just because the tracker runs there.")

    return {
        "tracking_enabled": True,
        "source_fps": _round(source_fps),
        "minimum_processing_fps": TRACKING_MIN_PROCESSING_FPS,
        "target_processing_fps": _round(target),
        "may_increase_to_fps": _round(source_fps if source_fps and source_fps > target else None),
        "target_submission_hz": _round(submission),
        "source_limited": source_limited,
        "rationale": rationale,
    }


def assess(plan: dict, processing_fps: float | None,
           submission_hz: float | None = None, device: str | None = None) -> dict:
    """Compare what a worker achieved against what it should achieve.

    Deliberately separate from availability. A worker running at 4 FPS is
    producing real, usable observations; it is a performance finding, never a
    reason to call the perception missing or the camera unusable.
    """
    target = plan.get("target_processing_fps")
    achieved = clean_fps(processing_fps)
    if achieved is None:
        return {
            "state": "unreported",
            "target_processing_fps": target,
            "processing_fps": None,
            "submission_hz": _round(clean_fps(submission_hz)),
            "device": device,
            "reason": ("The worker does not report processing_fps in its heartbeat metrics, so "
                       "its tracking rate is unknown — arriving samples alone do not prove it."),
            "likely_causes": [],
        }

    result = {
        "state": "ok",
        "target_processing_fps": target,
        "processing_fps": _round(achieved),
        "submission_hz": _round(clean_fps(submission_hz)),
        "device": device,
        "reason": "",
        "likely_causes": [],
    }
    if plan.get("tracking_enabled") and plan.get("source_limited"):
        result["state"] = "source_limited"
        result["reason"] = (
            f"The source delivers only {plan['source_fps']:g} FPS, so "
            f"{TRACKING_MIN_PROCESSING_FPS:g} FPS is unreachable here. This is a source "
            "limitation, not a worker fault; tracking quality is correspondingly weaker.")
        return result
    if target and achieved < target * TOLERANCE:
        result["state"] = "below_target"
        result["reason"] = (
            f"Processing {achieved:g} FPS against a {target:g} FPS target"
            + (f" while the source supplies {plan['source_fps']:g} FPS" if plan.get("source_fps")
               else "")
            + ". Tracking association degrades before sample delivery does, so this is not "
              "visible in the sample rate alone.")
        result["likely_causes"] = (
            [LIKELY_CAUSES[0], *LIKELY_CAUSES[1:]] if (device or "").lower() == "cpu"
            else list(LIKELY_CAUSES))
    return result


def acceleration_plan() -> dict:
    """What to check locally before starting a heavy worker, and how to decide.

    ManySight cannot answer any of this: the worker runs on the caller's machine,
    possibly not the machine serving this response. So the platform supplies the
    decision procedure and the worker executes it.
    """
    return {
        "decided_by": "the caller's own machine, never ManySight",
        "why": ("ManySight runs no models and never executes worker code, so it cannot detect "
                "your GPU. Probe before starting a tracking worker rather than after it "
                "underperforms."),
        "probe": {
            "sdk": ("python -c \"import sys; sys.path.insert(0,'sdk/python'); import manysight, "
                    "json; print(json.dumps(manysight.probe_perception_runtime(), indent=2))\""),
            "shell": "nvidia-smi",
            "python": [
                "torch.cuda.is_available()",
                "torch.cuda.get_device_name(0)",
                "torch.version.cuda   # what torch was built against",
                "torch.cuda.get_device_capability(0)   # FP16 is worth it from (7, 0)",
            ],
            "run_it_in": ("the interpreter that will actually run the worker — a base "
                          "environment answering 'yes' proves nothing about the venv you start"),
        },
        "device_preference": ["cuda", "cpu"],
        "fp16": ("Only on a CUDA device with compute capability >= 7.0, and only after "
                 "validating output. Never enable FP16 on a CPU path or an unvalidated runtime."),
        "cpu_fallback": {
            "supported": True,
            "rule": ("CUDA is an optimization, not a requirement. On CPU, target the best rate "
                     "you can actually sustain, measure it, and say plainly if it is below "
                     f"{TRACKING_MIN_PROCESSING_FPS:g} FPS."),
            "do_not": "Do not fake compliance by claiming the target rate you did not measure.",
        },
        "not_a_prerequisite": (
            "Camera availability, perception runnability, and performance capability are three "
            "separate questions. A missing GPU lowers the achievable rate; it never makes a "
            "camera unusable or perception impossible."),
        "framework": ("Reuse the worker's existing model and runtime. Do not introduce another "
                      "ML framework to enable acceleration."),
    }


def environment_plan() -> dict:
    """Reuse-first guidance for picking the interpreter that runs the worker."""
    return {
        "reuse_first": True,
        "order": [
            "Look for an existing project virtualenv or conda environment that already has the "
            "accelerated dependencies and the model weights.",
            "Verify CUDA inside that specific interpreter, not in the base environment.",
            "Only create a new environment if no existing one is compatible.",
        ],
        "never": [
            "Do not assume the global or base interpreter is the right one.",
            "Do not build a fresh environment by reflex when a working one exists.",
            "Do not ask the user which environment to use, whether they have CUDA, or to start "
            "the worker for you, when you have a shell and can find out or do it yourself.",
        ],
        "note": "This is guidance for your own shell; ManySight executes nothing on your behalf.",
    }


def readiness_axes() -> dict:
    """The three independent questions that a single 'is it working?' hides."""
    return {
        "camera_available": ("Is the source configured and reachable from the worker machine? "
                             "Independent of any GPU."),
        "perception_runnable": ("Can a worker run the model at all here? CPU is a valid answer; "
                                "CUDA is never required to start."),
        "performance_capable": ("Is the sustained processing rate good enough for the workload? "
                                f"For tracking that means >= {TRACKING_MIN_PROCESSING_FPS:g} FPS "
                                "when the source permits."),
        "note": ("Never report a camera as unusable because CUDA is unavailable. Report the "
                 "measured limitation and the acceleration path instead."),
    }
