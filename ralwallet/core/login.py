# регистрация и вход
import logging

from redis.asyncio import Redis
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ralwallet.api_models import UserCreate
from ralwallet.auth import check_password, hash_password, make_token
from ralwallet.errors import Conflict, TooManyAttempts, Unauthorized
from ralwallet.limiter import attempt, reset_attempts
from ralwallet.logs import mask_email
from ralwallet.repositories import users as users_repo
from ralwallet.tables import User

log = logging.getLogger(__name__)


async def register(session: AsyncSession, data: UserCreate) -> User:
    user = User(
        email=data.email.lower(),
        password_hash=await hash_password(data.password),
        last_name=data.last_name,
        first_name=data.first_name,
        middle_name=data.middle_name,
        birth_date=data.birth_date,
        phone=data.phone,
    )
    users_repo.add(session, user)
    try:
        await session.commit()
    except IntegrityError as e:
        #что совпало, email или телефон
        await session.rollback()
        if "phone" in str(e.orig):
            raise Conflict("этот телефон уже привязан к другому пользователю") from None
        raise Conflict("пользователь с таким email уже есть") from None
    await session.refresh(user)
    log.info("регистрация user=%s", user.id)
    return user


async def login(
    session: AsyncSession, redis: Redis, email: str, password: str, ip: str, agent: str | None
) -> str:
    email = email.lower()

    # сначала +1 попытка, потом проверка
    wait = await attempt(redis, f"login:{email}")
    if wait:
        log.warning("вход заблокирован после неудачных попыток %s", mask_email(email))
        raise TooManyAttempts(f"слишком много неудачных попыток, попробуйте через {(wait + 59) // 60} мин")

    user = await users_repo.by_email(session, email)

    #bcrypt и для левого email, чтоб по времени не палилось
    password_ok = await check_password(password, user.password_hash if user else None)
    if user is None or not password_ok:
        if user is not None:
            users_repo.add_login(session, user.id, False, ip, agent)
            await session.commit()
        # email в лог не целиком
        log.info("неудачный вход %s ip=%s", mask_email(email), ip)
        raise Unauthorized("неверный email или пароль")

    await reset_attempts(redis, f"login:{email}")
    users_repo.add_login(session, user.id, True, ip, agent)
    await session.commit()
    log.info("вход user=%s ip=%s", user.id, ip)
    return make_token(user)
