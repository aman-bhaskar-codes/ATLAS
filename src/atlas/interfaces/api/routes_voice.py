"""Voice API routes — speech in / speech out over HTTP + WebSocket.

Transport layer (interfaces): this module MAY call ``orchestrator.run()``. The
pure audio<->text engine lives in ``capabilities/voice``; here we turn a
transcript into an ``InboundEvent(source="voice")``, run it through the one
safety-governed orchestrator funnel, and speak the answer back.

PRIVACY: audio posted here is forwarded to third-party STT/TTS providers.

All endpoints degrade cleanly to HTTP 503 when the voice subsystem is disabled
or unconfigured (``atlas.voice_service is None``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from atlas.app import Atlas
from atlas.infra.logging import get_logger
from atlas.infra.types import InboundEvent
from atlas.interfaces.api.dependencies import get_atlas

_log = get_logger("atlas.interfaces.api.voice")

router = APIRouter(tags=["voice"])


class SpeakRequest(BaseModel):
    text: str
    language: str | None = None


class TranscribeResponse(BaseModel):
    text: str
    confidence: float


def _require_voice(atlas: Atlas) -> object:
    service = atlas.voice_service
    if service is None:
        raise HTTPException(status_code=503, detail="voice subsystem is disabled or unconfigured")
    return service


@router.post("/voice/speak")
async def speak(body: SpeakRequest, atlas: Atlas = Depends(get_atlas)) -> StreamingResponse:
    """Synthesize ``text`` to streamed audio bytes."""
    service = _require_voice(atlas)

    async def _stream() -> AsyncIterator[bytes]:
        async for chunk in service.speak(body.text, body.language):  # type: ignore[attr-defined]
            if chunk.data:
                yield chunk.data

    return StreamingResponse(_stream(), media_type="audio/mpeg")


@router.post("/voice/transcribe", response_model=TranscribeResponse)
async def transcribe(request: Request, atlas: Atlas = Depends(get_atlas)) -> TranscribeResponse:
    """Transcribe a posted audio body (raw bytes) to text."""
    service = _require_voice(atlas)
    audio = await request.body()
    result = await service.transcribe(audio)  # type: ignore[attr-defined]
    return TranscribeResponse(text=result.text, confidence=result.confidence)


@router.websocket("/ws/voice")
async def voice_ws(websocket: WebSocket) -> None:
    """Bidirectional voice loop: audio in -> STT -> orchestrator -> TTS audio out.

    Protocol (minimal): the client streams raw PCM binary frames and sends a
    text frame ``"__end__"`` to mark end-of-utterance. The server transcribes,
    runs the orchestrator, then streams synthesized audio binary frames back,
    terminated by a text frame ``"__done__"``.
    """
    atlas = cast_atlas(websocket)
    service = atlas.voice_service
    if service is None:
        await websocket.close(code=1011, reason="voice disabled")
        return

    await websocket.accept()
    try:
        while True:
            frames: list[bytes] = []
            # Collect one utterance.
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    return
                if (text := message.get("text")) is not None:
                    if text == "__end__":
                        break
                    continue
                if (data := message.get("bytes")) is not None:
                    frames.append(data)

            audio = b"".join(frames)
            if not audio:
                continue

            result = await service.transcribe(audio)
            transcript = result.text.strip()
            if not transcript:
                await websocket.send_text("__done__")
                continue

            answer = await _run_transcript(atlas, transcript)
            async for chunk in service.speak(answer, None):
                if chunk.data:
                    await websocket.send_bytes(chunk.data)
            await websocket.send_text("__done__")
    except WebSocketDisconnect:
        return


async def _run_transcript(atlas: Atlas, transcript: str) -> str:
    """Run a spoken request through the orchestrator; return a speakable answer."""
    event = InboundEvent(
        correlation_id=atlas.ids.correlation_id(),
        source="voice",
        content=transcript,
    )
    result = await atlas.orchestrator.run(event)
    if result.ok and result.answer:
        return result.answer
    return result.error or "I could not complete that request."


def cast_atlas(websocket: WebSocket) -> Atlas:
    """WebSocket has no Depends resolution for Request-based providers."""
    from typing import cast

    return cast(Atlas, websocket.app.state.atlas)
