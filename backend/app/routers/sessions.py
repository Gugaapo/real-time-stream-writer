"""HTTP API for personas, saved chapters, and the live writing stream."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.personas import PERSONAS, STYLE_IDS, clean_custom_prompt

router = APIRouter(prefix="/api/v1")

LOTADO = "O livrinho está lotado. Tenta de novo daqui a pouco."


class CreateSession(BaseModel):
    style: str = Field(default="novel")
    prompt: str = ""


@router.get("/health")
async def health() -> dict:
    return {"ok": True}


@router.get("/personas")
async def personas() -> dict:
    return {"personas": list(PERSONAS)}


@router.post("/sessions")
async def create_session(body: CreateSession, request: Request) -> dict:
    style = (body.style or "").strip()
    if style not in STYLE_IDS:
        raise HTTPException(status_code=422, detail="Voz desconhecida.")
    custom = ""
    if style == "custom":
        try:
            custom = clean_custom_prompt(body.prompt)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return request.app.state.store.create_session(style, custom)


@router.get("/sessions/{session_id}")
async def read_session(session_id: str, request: Request) -> dict:
    row = request.app.state.store.get_session(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Capítulo não encontrado.")
    return row


@router.delete("/sessions/{session_id}/paragraphs")
async def clear_paragraphs(session_id: str, request: Request) -> dict:
    row = request.app.state.runtime.clear_text(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Capítulo não encontrado.")
    return row


@router.get("/sessions/{session_id}/events")
async def session_events(session_id: str, request: Request):
    runtime = request.app.state.runtime
    slot = runtime.acquire(session_id)
    if slot is None:
        raise HTTPException(status_code=404, detail="Capítulo não encontrado.")
    if slot is False:
        raise HTTPException(status_code=503, detail=LOTADO)
    return StreamingResponse(
        runtime.iter_events(session_id, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
