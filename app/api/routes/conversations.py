"""Conversations — server-side chat history, scoped to a vault.

The ask flow persists every exchange here, and the frontend renders past
conversations from these endpoints so history survives reloads and browsers.
"""

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, HTTPException

from app.core.logging import get_logger
from app.db.models import Conversation, Message
from app.db.session import get_session
from app.schemas.api import ConversationCreate, ConversationDetail, ConversationOut, MessageOut

logger = get_logger(__name__)
router = APIRouter(tags=["conversations"])


def _to_out(conv: Conversation, message_count: int = 0) -> ConversationOut:
    return ConversationOut(
        id=conv.id,
        vault_name=conv.vault_name,
        title=conv.title,
        message_count=message_count,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
    )


async def _message_out(m: Message) -> MessageOut:
    return MessageOut(
        id=m.id,
        role=m.role,
        content=m.content,
        sources=m.sources,
        citations=m.citations,
        usage=m.usage,
        error=m.error,
        cached=m.cached,
        created_at=m.created_at,
    )


async def _get_conversation(session: AsyncSession, conv_id: str) -> Conversation:
    conv = await session.get(Conversation, conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return conv


@router.get("/vaults/{name}/conversations", response_model=list[ConversationOut])
async def list_conversations(name: str, session: AsyncSession = Depends(get_session)) -> list[ConversationOut]:
    convs = (
        (await session.execute(select(Conversation).where(Conversation.vault_name == name).order_by(Conversation.updated_at.desc())))
        .scalars()
        .all()
    )
    if not convs:
        return []
    ids = [c.id for c in convs]
    counts = dict(
        (
            await session.execute(
                select(Message.conversation_id, func.count(Message.id))
                .where(Message.conversation_id.in_(ids))
                .group_by(Message.conversation_id)
            )
        ).all()
    )
    return [_to_out(c, counts.get(c.id, 0)) for c in convs]


@router.post("/vaults/{name}/conversations", response_model=ConversationOut, status_code=201)
async def create_conversation(
    name: str, payload: ConversationCreate, session: AsyncSession = Depends(get_session)
) -> ConversationOut:
    conv = Conversation(vault_name=name, title=(payload.title or "New conversation").strip() or "New conversation")
    session.add(conv)
    await session.commit()
    return _to_out(conv)


@router.get("/conversations/{conv_id}", response_model=ConversationDetail)
async def conversation_detail(conv_id: str, session: AsyncSession = Depends(get_session)) -> ConversationDetail:
    conv = await _get_conversation(session, conv_id)
    messages = (
        (await session.execute(select(Message).where(Message.conversation_id == conv_id).order_by(Message.created_at, Message.id)))
        .scalars()
        .all()
    )
    return ConversationDetail(
        id=conv.id,
        vault_name=conv.vault_name,
        title=conv.title,
        messages=[await _message_out(m) for m in messages],
        created_at=conv.created_at,
        updated_at=conv.updated_at,
    )


@router.get("/conversations/{conv_id}/messages", response_model=list[MessageOut])
async def conversation_messages(conv_id: str, session: AsyncSession = Depends(get_session)) -> list[MessageOut]:
    await _get_conversation(session, conv_id)
    messages = (
        (await session.execute(select(Message).where(Message.conversation_id == conv_id).order_by(Message.created_at, Message.id)))
        .scalars()
        .all()
    )
    return [await _message_out(m) for m in messages]


@router.patch("/conversations/{conv_id}", response_model=ConversationOut)
async def rename_conversation(
    conv_id: str, payload: ConversationCreate, session: AsyncSession = Depends(get_session)
) -> ConversationOut:
    conv = await _get_conversation(session, conv_id)
    if payload.title:
        conv.title = payload.title.strip() or conv.title  # updated_at bumps via onupdate
        await session.commit()
    return _to_out(conv)


@router.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    conv = await _get_conversation(session, conv_id)
    await session.execute(delete(Message).where(Message.conversation_id == conv_id))
    await session.delete(conv)
    await session.commit()
    return {"deleted": conv_id}


async def append_message(
    session: AsyncSession,
    conv_id: str,
    role: str,
    content: str,
    sources: dict | None = None,
    citations: dict | None = None,
    usage: dict | None = None,
    error: str | None = None,
    cached: bool = False,
) -> Message:
    """Shared helper used by the ask route to persist an exchange."""
    conv = await _get_conversation(session, conv_id)
    msg = Message(
        conversation_id=conv.id,
        role=role,
        content=content,
        sources=sources,
        citations=citations,
        usage=usage,
        error=error,
        cached=cached,
    )
    session.add(msg)
    # conversation.updated_at bumps automatically via onupdate
    await session.commit()
    return msg
