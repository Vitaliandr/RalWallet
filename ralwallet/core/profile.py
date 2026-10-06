import logging
from datetime import UTC, date, datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ralwallet.api_models import from_kopecks, to_kopecks
from ralwallet.auth import check_password, hash_password, make_token
from ralwallet.errors import AppError, Conflict, NotFound, TooManyAttempts
from ralwallet.limiter import attempt, reset_attempts
from ralwallet.repositories import accounts as accounts_repo
from ralwallet.repositories import transfers as transfers_repo
from ralwallet.repositories import users as users_repo
from ralwallet.tables import Account, LoginEvent, User

log = logging.getLogger(__name__)


def age(birth: date) -> int:
    today = date.today()
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


def short_name(u: User) -> str | None:
    #"Анна П." полное фио чужим не надо
    if not u.first_name:
        return None
    if u.last_name:
        return f"{u.first_name} {u.last_name[0]}."
    return u.first_name


async def update_profile(session: AsyncSession, user: User, fields: dict[str, Any]) -> User:
    # только присланные поля, None = очистить
    for required in ("last_name", "first_name", "birth_date"):
        if required in fields and fields[required] is None:
            raise AppError("фамилию, имя и дату рождения удалить нельзя")

    if fields.get("main_account_id") is not None:
        acc = await accounts_repo.get(session, fields["main_account_id"])
        if acc is None or acc.user_id != user.id:
            raise NotFound("счёт не найден")

    if "daily_limit" in fields:
        limit = fields["daily_limit"]
        fields["daily_limit"] = to_kopecks(limit) if limit is not None else None

    for k, v in fields.items():
        setattr(user, k, v)

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        # уникальный тут только телефон
        raise Conflict("этот телефон уже привязан к другому пользователю") from None
    await session.refresh(user)
    return user


async def _confirm_password(redis: Redis, user: User, password: str) -> None:
    wait = await attempt(redis, f"confirm:{user.id}")
    if wait:
        log.warning("подтверждение паролем заблокировано user=%s", user.id)
        raise TooManyAttempts(f"слишком много неверных паролей, попробуйте через {(wait + 59) // 60} мин")
    #400 а не 401, иначе фронт выкинет на логин
    if not await check_password(password, user.password_hash):
        log.warning("неверный пароль при подтверждении user=%s", user.id)
        raise AppError("неверный пароль")
    await reset_attempts(redis, f"confirm:{user.id}")


async def change_password(session: AsyncSession, redis: Redis, user: User, current: str, new: str) -> str:
    await _confirm_password(redis, user, current)
    if current == new:
        raise AppError("новый пароль совпадает со старым")

    user.password_hash = await hash_password(new)
    # все старые токены сразу протухнут
    user.token_version += 1
    await session.commit()
    log.info("смена пароля user=%s, старые токены отозваны", user.id)
    #а этому устройству новый
    return make_token(user)


async def change_email(session: AsyncSession, redis: Redis, user: User, new_email: str, password: str) -> User:
    await _confirm_password(redis, user, password)
    new_email = new_email.lower()
    if new_email == user.email:
        raise AppError("это и есть ваш текущий email")

    user.email = new_email
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise Conflict("пользователь с таким email уже есть") from None
    await session.refresh(user)
    log.info("смена email user=%s", user.id)
    return user


async def login_history(session: AsyncSession, user_id: int, limit: int = 20) -> list[LoginEvent]:
    return await users_repo.logins(session, user_id, limit)


async def month_stats(session: AsyncSession, user: User) -> dict[str, Any]:
    now = datetime.now(UTC)
    # TODO: месяц по UTC
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    by_cur = await transfers_repo.month_totals(session, user.id, month_start)
    return {
        "registered_at": user.created_at,
        "accounts_count": await accounts_repo.count_of_user(session, user.id),
        "month": [
            {"currency": cur, "sent": from_kopecks(s), "received": from_kopecks(r)}
            for cur, (s, r) in sorted(by_cur.items())
        ],
    }


async def find_recipient(
    session: AsyncSession, account_id: int | None = None, phone: str | None = None
) -> tuple[Account, User]:
    #(счёт, владелец), по телефону берём основной счёт
    if phone is not None:
        owner = await users_repo.by_phone(session, phone)
        if owner is None:
            raise NotFound("пользователь с таким телефоном не найден")
        main = await accounts_repo.get(session, owner.main_account_id) if owner.main_account_id else None
        if main is None:
            raise NotFound("у получателя нет основного счёта")
        return main, owner

    acc = await accounts_repo.get(session, account_id) if account_id is not None else None
    if acc is None:
        raise NotFound("счёт зачисления не найден")
    acc_owner = await users_repo.get(session, acc.user_id)
    if acc_owner is None:
        raise NotFound("счёт зачисления не найден")
    return acc, acc_owner
