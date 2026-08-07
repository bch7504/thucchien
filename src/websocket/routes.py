import asyncio

import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.auth.security import decode_access_token
from src.db import session as db_session
from src.db.models import User
from src.services import chat_service, proactive_service
from src.websocket.manager import manager

router = APIRouter()

# asyncio.create_task() results must be held onto - an unreferenced task can be garbage
# collected mid-run. Fire-and-forget background jobs (proactive detection) get parked here
# and self-remove via add_done_callback once finished.
_background_tasks: set[asyncio.Task] = set()


def _run_in_background(coro) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


@router.websocket("/ws")
async def chat_websocket(websocket: WebSocket) -> None:
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4001)
        return
    try:
        user_id = decode_access_token(token)
    except jwt.PyJWTError:
        await websocket.close(code=4001)
        return

    async with db_session.async_session_maker() as db:
        user = await db.get(User, user_id)
        if user is None:
            await websocket.close(code=4001)
            return

    await manager.connect(user_id, websocket)
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") != "send_message":
                await websocket.send_json({"type": "error", "detail": "Unknown message type"})
                continue

            conversation_id = data.get("conversation_id")
            content = (data.get("content") or "").strip()
            if not conversation_id or not content:
                await websocket.send_json({"type": "error", "detail": "conversation_id and content are required"})
                continue

            async with db_session.async_session_maker() as db:
                try:
                    await chat_service.assert_participant(db, conversation_id, user_id)
                except Exception:
                    await websocket.send_json({"type": "error", "detail": "Not a participant of this conversation"})
                    continue

                message = await chat_service.create_message(db, conversation_id, user_id, content)
                sender = await db.get(User, user_id)
                message_out = chat_service.serialize_message(message, sender)
                participant_ids = await chat_service.get_participant_ids(db, conversation_id)

            await manager.broadcast_to_users(
                participant_ids, {"type": "new_message", "message": message_out.model_dump()}
            )
            _run_in_background(
                proactive_service.maybe_suggest_task(
                    conversation_id=conversation_id, sender_id=user_id, content=content
                )
            )
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(user_id, websocket)
