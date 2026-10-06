from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ralwallet.tables import Account


async def get(session: AsyncSession, account_id: int) -> Account | None:
    return await session.get(Account, account_id)


async def of_user(session: AsyncSession, user_id: int) -> list[Account]:
    #id случайный, сортируем по дате
    q = select(Account).where(Account.user_id == user_id).order_by(Account.created_at, Account.id)
    res = await session.execute(q)
    return list(res.scalars())


async def count_of_user(session: AsyncSession, user_id: int) -> int:
    n = await session.scalar(select(func.count()).select_from(Account).where(Account.user_id == user_id))
    return n or 0


async def lock(session: AsyncSession, account_id: int) -> Account | None:
    # без populate_existing тут будет старый баланс!! ловил на тесте
    res = await session.execute(
        select(Account).where(Account.id == account_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return res.scalar_one_or_none()


def add(session: AsyncSession, acc: Account) -> None:
    session.add(acc)


async def set_balance_if_version(session: AsyncSession, acc: Account, new_balance: int) -> bool:
    #оптимистичная блокировка, False = кто-то успел раньше
    res = await session.execute(
        update(Account)
        .where(Account.id == acc.id, Account.version == acc.version)
        .values(balance=new_balance, version=acc.version + 1)
        .execution_options(synchronize_session=False)
    )
    return res.rowcount == 1
