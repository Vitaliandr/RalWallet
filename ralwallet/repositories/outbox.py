from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ralwallet.tables import OutboxEvent


def add(session: AsyncSession, topic: str, key: str, payload: dict[str, Any]) -> None:
    session.add(OutboxEvent(topic=topic, key=key, payload=payload))


async def take_unsent(session: AsyncSession, limit: int) -> list[OutboxEvent]:
    # SKIP LOCKED, несколько relay не возьмут одно и то же
    res = await session.execute(
        select(OutboxEvent)
        .where(OutboxEvent.sent_at.is_(None))
        .order_by(OutboxEvent.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list(res.scalars())


def mark_sent(event: OutboxEvent) -> None:
    event.sent_at = datetime.now(UTC)
