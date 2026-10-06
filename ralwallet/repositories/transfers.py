from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from ralwallet.tables import Account, Transfer


async def by_key(session: AsyncSession, user_id: int, key: str) -> Transfer | None:
    res = await session.execute(
        select(Transfer).where(Transfer.user_id == user_id, Transfer.idempotency_key == key)
    )
    return res.scalar_one_or_none()


async def visible_to(session: AsyncSession, transfer_id: int, user_id: int) -> Transfer | None:
    #перевод видят и отправитель и получатель
    my_accounts = select(Account.id).where(Account.user_id == user_id)
    res = await session.execute(
        select(Transfer).where(
            Transfer.id == transfer_id,
            or_(Transfer.from_account_id.in_(my_accounts), Transfer.to_account_id.in_(my_accounts)),
        )
    )
    return res.scalar_one_or_none()


async def add(session: AsyncSession, transfer: Transfer) -> None:
    session.add(transfer)
    await session.flush()  #нужен transfer.id


async def sent_to_others(session: AsyncSession, user_id: int, currency: str, since: datetime) -> int:
    # только переводы другим людям
    q = (
        select(func.coalesce(func.sum(Transfer.amount), 0))
        .join(Account, Account.id == Transfer.to_account_id)
        .where(
            Transfer.user_id == user_id,
            Transfer.currency == currency,
            Transfer.created_at >= since,
            Account.user_id != user_id,
        )
    )
    return await session.scalar(q) or 0


async def month_totals(session: AsyncSession, user_id: int, since: datetime) -> dict[str, list[int]]:
    #{валюта: [отправлено, получено]}, свои переводы не считаем
    src = aliased(Account)
    dst = aliased(Account)
    base = (
        select(Transfer.currency, func.sum(Transfer.amount))
        .join(src, src.id == Transfer.from_account_id)
        .join(dst, dst.id == Transfer.to_account_id)
        .where(Transfer.created_at >= since)
        .group_by(Transfer.currency)
    )
    sent = await session.execute(base.where(src.user_id == user_id, dst.user_id != user_id))
    received = await session.execute(base.where(dst.user_id == user_id, src.user_id != user_id))

    by_cur: dict[str, list[int]] = {}
    for cur, total in sent:
        by_cur.setdefault(cur, [0, 0])[0] = total
    for cur, total in received:
        by_cur.setdefault(cur, [0, 0])[1] = total
    return by_cur
