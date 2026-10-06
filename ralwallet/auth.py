import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from ralwallet.database import get_session
from ralwallet.limiter import get_redis
from ralwallet.repositories import users as users_repo
from ralwallet.settings import settings
from ralwallet.tables import User

ALGORITHM = "HS256"
BCRYPT_MAX = 72  # в байтах

bearer = HTTPBearer(auto_error=False)

#для несуществующих email, чтоб время ответа было одинаковое
_DUMMY_HASH = bcrypt.hashpw(uuid.uuid4().hex.encode(), bcrypt.gensalt()).decode()


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _check(password: str, hashed: str) -> bool:
    if len(password.encode()) > BCRYPT_MAX:
        # такой пароль и так не сохранили бы
        return False
    return bcrypt.checkpw(password.encode(), hashed.encode())


#bcrypt медленный и блочит event loop, поэтому в поток
async def hash_password(password: str) -> str:
    return await asyncio.to_thread(_hash, password)


async def check_password(password: str, hashed: str | None) -> bool:
    if hashed is None:
        await asyncio.to_thread(_check, password, _DUMMY_HASH)
        return False
    return await asyncio.to_thread(_check, password, hashed)


def make_token(user: User) -> str:
    exp = datetime.now(UTC) + timedelta(minutes=settings.jwt_ttl_minutes)
    payload = {
        "sub": str(user.id),
        "ver": user.token_version,  # для выхода со всех устройств
        "jti": uuid.uuid4().hex,  #для logout
        "exp": exp,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def read_token(token: str) -> dict[str, Any] | None:
    try:
        # без этого пролезет alg=none
        data = jwt.decode(
            token, settings.jwt_secret, algorithms=[ALGORITHM], options={"require": ["exp", "sub", "jti"]}
        )
        return {
            "user_id": int(data["sub"]),
            "ver": int(data.get("ver", 0)),
            "jti": str(data["jti"]),
            "exp": data["exp"],
        }
    except (jwt.PyJWTError, ValueError, TypeError):
        return None


async def revoke(redis: Redis, claims: dict[str, Any]) -> None:
    #пока токен сам не протухнет
    ttl = int(claims["exp"] - datetime.now(UTC).timestamp())
    if ttl > 0:
        await redis.set(f"revoked:{claims['jti']}", 1, ex=ttl)


async def token_claims(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    redis: Redis = Depends(get_redis),
) -> dict[str, Any]:
    if creds is None:
        raise HTTPException(401, "нужна авторизация")
    claims = read_token(creds.credentials)
    if claims is None or await redis.exists(f"revoked:{claims['jti']}"):
        raise HTTPException(401, "неверный или просроченный токен")
    return claims


async def current_user(
    claims: dict[str, Any] = Depends(token_claims),
    session: AsyncSession = Depends(get_session),
) -> User:
    user = await users_repo.get(session, claims["user_id"])
    # пароль меняли, токен уже старый
    if user is None or user.token_version != claims["ver"]:
        raise HTTPException(401, "неверный или просроченный токен")
    return user
