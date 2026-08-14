"""Guided playable-demo lifecycle and controlled local media access."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..services import demo_runtime
from ..services import demo_media

router = APIRouter(prefix="/demo", tags=["guided-demo"])


class SessionIn(BaseModel):
    mode: str = "guided"


class PromotionIn(BaseModel):
    include_recorded_observations: bool = False


class PracticeCalibrationIn(BaseModel):
    source_id: int


@router.get("/assets")
def get_asset_status():
    return demo_runtime.asset_status()


@router.get("/stream-supervisor")
def get_stream_supervisor():
    return demo_media.status()


@router.get("/sessions/active")
def get_active_session():
    return demo_runtime.active_session()


@router.post("/sessions", status_code=201, summary="Create an isolated guided-demo workspace")
def create_session(body: SessionIn, request: Request):
    return demo_runtime.create_session(str(request.base_url).rstrip("/"), body.mode)


@router.get("/sessions/{session_id}")
def get_session(session_id: str):
    return demo_runtime.get_session(session_id)


@router.get("/sessions/{session_id}/replay-cache", include_in_schema=False)
def get_replay_cache(session_id: str):
    return demo_runtime.replay_cache(session_id)


@router.post("/sessions/{session_id}/start")
async def start_session(session_id: str):
    return demo_runtime.start(session_id)


@router.post("/sessions/{session_id}/pause")
def pause_session(session_id: str):
    return demo_runtime.pause(session_id)


@router.post("/sessions/{session_id}/restore-practice-calibration")
def restore_practice_calibration(session_id: str, body: PracticeCalibrationIn):
    return demo_runtime.restore_practice_calibration(session_id, body.source_id)


@router.post("/sessions/{session_id}/discard")
async def discard_session(session_id: str):
    return await demo_runtime.discard(session_id)


@router.post("/sessions/{session_id}/promote", summary="Promote the demo camera and space setup")
async def promote_session(session_id: str, body: PromotionIn, request: Request):
    return await demo_runtime.promote(
        session_id, str(request.base_url).rstrip("/"), body.include_recorded_observations)


@router.get("/media/{camera_key}.mp4", include_in_schema=False)
def get_media(camera_key: str, demo_session: str):
    path = demo_runtime.media_path(demo_session, camera_key)
    return FileResponse(path, media_type="video/mp4")


@router.get("/sessions/{session_id}/camera-evidence/{camera_key}", include_in_schema=False)
def get_camera_evidence(session_id: str, camera_key: str):
    return demo_runtime.camera_evidence(session_id, camera_key)


@router.get("/plan.png", include_in_schema=False)
def get_plan(demo_session: str):
    return FileResponse(demo_runtime.plan_path(demo_session), media_type="image/png")
