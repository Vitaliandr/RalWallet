import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from ralwallet.api_models import (
    EmailChange,
    LoginEventOut,
    LoginIn,
    PasswordChange,
    ProfileUpdate,
    StatsOut,
    TokenOut,
    UserCreate,
    UserOut,
)
from ralwallet.auth import current_user, revoke, token_claims
from ralwallet.core import login as login_service
from ralwallet.core import profile
from ralwallet.database import get_session
from ralwallet.limiter import get_redis, hit
from ralwallet.settings import settings
from ralwallet.tables import LoginEvent, User

router = APIRouter(tags=["users"])
log = logging.getLogger(__name__)


def client_ip(request: Request) -> str:
    # X-Forwarded-For не читаем, подделывается
    return request.client.host if request.client else "unknown"


@router.post("/users", response_model=UserOut, status_code=201)
async def register(
    data: UserCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> UserOut:
    if not await hit(redis, f"reg:{client_ip(request)}", settings.registrations_per_ip_minute):
        log.warning("лимит регистраций с ip=%s", client_ip(request))
        raise HTTPException(429, "слишком много регистраций, подождите минуту")
    user = await login_service.register(session, data)
    return UserOut.from_model(user)


@router.post("/auth/login", response_model=TokenOut)
async def login(
    data: LoginIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> TokenOut:
    ip = client_ip(request)
    #лимит с одного ip
    if not await hit(redis, f"login_ip:{ip}", settings.logins_per_ip_minute):
        log.warning("лимит входов с ip=%s", ip)
        raise HTTPException(429, "слишком много попыток входа, подождите минуту")

    token = await login_service.login(
        session, redis, data.email, data.password, ip, request.headers.get("user-agent")
    )
    return TokenOut(access_token=token)


@router.post("/auth/logout", status_code=204)
async def logout(claims: dict[str, Any] = Depends(token_claims), redis: Redis = Depends(get_redis)) -> None:
    await revoke(redis, claims)
    log.info("выход user=%s", claims["user_id"])


@router.get("/users/me", response_model=UserOut)
async def me(user: User = Depends(current_user)) -> UserOut:
    return UserOut.from_model(user)


@router.patch("/users/me", response_model=UserOut)
async def update_me(
    data: ProfileUpdate, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> UserOut:
    user = await profile.update_profile(session, user, data.model_dump(exclude_unset=True))
    return UserOut.from_model(user)


# тут тоже считаем попытки (на случай украденного токена)
@router.post("/users/me/password", response_model=TokenOut)
async def change_password(
    data: PasswordChange,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> TokenOut:
    token = await profile.change_password(session, redis, user, data.current_password, data.new_password)
    return TokenOut(access_token=token)


@router.post("/users/me/email", response_model=UserOut)
async def change_email(
    data: EmailChange,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> UserOut:
    user = await profile.change_email(session, redis, user, data.new_email, data.password)
    return UserOut.from_model(user)


@router.get("/users/me/logins", response_model=list[LoginEventOut])
async def my_logins(
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> list[LoginEvent]:
    return await profile.login_history(session, user.id)


@router.get("/users/me/stats", response_model=StatsOut)
async def my_stats(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    return await profile.month_stats(session, user)
