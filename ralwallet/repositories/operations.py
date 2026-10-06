from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ralwallet.tables import Operation, Transfer


def add(session: AsyncSession, *ops: Operation) -> None:
    session.add_all(ops)


async def history(
    session: AsyncSession, account_id: int, limit: int, before_id: int | None
) -> list[tuple[Operation, Transfer | None]]:
    # join ради второго счёта
    q = (
        select(Operation, Transfer)
        .outerjoin(Transfer, Operation.transfer_id == Transfer.id)
        .where(Operation.account_id == account_id)
    )
    #keyset а не offset, offset тормозит
    if before_id is not None:
        q = q.where(Operation.id < before_id)
    q = q.order_by(Operation.id.desc()).limit(limit)

    res = await session.execute(q)
    return [(op, t) for op, t in res.all()]
