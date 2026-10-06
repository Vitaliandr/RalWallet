import logging
import secrets

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ralwallet.core.profile import age
from ralwallet.errors import AppError, Conflict, NotFound
from ralwallet.repositories import accounts as accounts_repo
from ralwallet.repositories import operations as operations_repo
from ralwallet.repositories import users as users_repo
from ralwallet.tables import Account, Operation, Transfer, User

MAX_RETRIES = 5

log = logging.getLogger(__name__)


def new_account_number() -> int:
    #10 цифр. secrets а не random
    return 1_000_000_000 + secrets.randbelow(9_000_000_000)


async def create_account(session: AsyncSession, user: User, currency: str) -> Account:
    # как в банках, только с 18
    if user.birth_date is None:
        raise AppError("укажите дату рождения в профиле, счёт открывается только с 18 лет")
    if age(user.birth_date) < 18:
        raise AppError("счёт можно открыть только с 18 лет")

    user_id = user.id
    #совпадение почти нереально но мало ли
    for _ in range(MAX_RETRIES):
        acc = Account(id=new_account_number(), user_id=user_id, currency=currency, balance=0, version=0)
        accounts_repo.add(session, acc)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            # после rollback перечитываем
            fresh = await users_repo.get(session, user_id)
            if fresh is None:
                raise NotFound("пользователь не найден") from None
            user = fresh
            continue

        #первый счёт сразу основной
        if user.main_account_id is None:
            user.main_account_id = acc.id
        await session.commit()
        await session.refresh(acc)
        log.info("открыт счёт %s (%s) user=%s", acc.id, acc.currency, user_id)
        return acc
    raise Conflict("не удалось открыть счёт, попробуйте ещё раз")


async def get_user_accounts(session: AsyncSession, user_id: int) -> list[Account]:
    return await accounts_repo.of_user(session, user_id)


async def get_user_account(session: AsyncSession, account_id: int, user_id: int) -> Account:
    acc = await accounts_repo.get(session, account_id)
    # чужой = 404, чтоб не перебирали id
    if acc is None or acc.user_id != user_id:
        raise NotFound("счёт не найден")
    return acc


async def deposit(session: AsyncSession, account_id: int, user_id: int, amount: int) -> Account:
    #оптимистичная блокировка тут для примера, в переводах FOR UPDATE
    for _ in range(MAX_RETRIES):
        acc = await get_user_account(session, account_id, user_id)
        new_balance = acc.balance + amount

        if await accounts_repo.set_balance_if_version(session, acc, new_balance):
            operations_repo.add(
                session, Operation(account_id=acc.id, kind="deposit", amount=amount, balance_after=new_balance)
            )
            await session.commit()
            await session.refresh(acc)
            log.info("пополнение счёта %s на %s коп", acc.id, amount)
            return acc

        log.info("пополнение счёта %s: версия поменялась, повторяем", account_id)
        # кто-то успел раньше, пробуем ещё
        await session.rollback()

    raise Conflict("не удалось пополнить счёт, попробуйте ещё раз")


async def get_operations(
    session: AsyncSession, account_id: int, user_id: int, limit: int = 50, before_id: int | None = None
) -> list[tuple[Operation, Transfer | None]]:
    await get_user_account(session, account_id, user_id)
    return await operations_repo.history(session, account_id, limit, before_id)
