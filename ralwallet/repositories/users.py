# коммитов тут нет, коммитит сервис
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ralwallet.tables import LoginEvent, User


async def get(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def by_email(session: AsyncSession, email: str) -> User | None:
    res = await session.execute(select(User).where(User.email == email))
    return res.scalar_one_or_none()


async def by_phone(session: AsyncSession, phone: str) -> User | None:
    res = await session.execute(select(User).where(User.phone == phone))
    return res.scalar_one_or_none()


async def lock(session: AsyncSession, user_id: int) -> User:
    #NO KEY UPDATE а не FOR UPDATE, чтоб не блочить вставки в logins
    # populate_existing т.к. юзер уже в сессии
    res = await session.execute(
        select(User).where(User.id == user_id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    return res.scalar_one()


def add(session: AsyncSession, user: User) -> None:
    session.add(user)


def add_login(session: AsyncSession, user_id: int, success: bool, ip: str | None, agent: str | None) -> None:
    session.add(LoginEvent(user_id=user_id, success=success, ip=ip, user_agent=(agent or "")[:255] or None))


async def logins(session: AsyncSession, user_id: int, limit: int) -> list[LoginEvent]:
    res = await session.execute(
        select(LoginEvent).where(LoginEvent.user_id == user_id).order_by(LoginEvent.id.desc()).limit(limit)
    )
    return list(res.scalars())
