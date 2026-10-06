import atexit
import os
import uuid


def _test_database_url() -> str:
    # TEST_DATABASE_URL или postgres в докере
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        return url
    from testcontainers.postgres import PostgresContainer

    pg = PostgresContainer("postgres:16-alpine", driver="asyncpg")
    pg.start()
    atexit.register(pg.stop)
    return pg.get_connection_url()


#до импорта app!
os.environ["DATABASE_URL"] = _test_database_url()
# свой секрет на каждый прогон
os.environ["JWT_SECRET"] = uuid.uuid4().hex + uuid.uuid4().hex

import pytest
from fakeredis import FakeAsyncRedis
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from ralwallet.database import Base, get_session
from ralwallet.limiter import get_redis
from ralwallet.main import app
from ralwallet.settings import settings


@pytest.fixture
async def engine():
    #NullPool, иначе соединения переживают event loop
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    async with engine.begin() as conn:
        # drop_all падает на старой схеме, проще так
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def client(engine):
    make_session = async_sessionmaker(engine, expire_on_commit=False)

    async def _session():
        async with make_session() as s:
            yield s

    redis = FakeAsyncRedis(decode_responses=True)
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_redis] = lambda: redis

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()


def user_data(email=None, password="password123", **extra):
    data = {
        "email": email or f"{uuid.uuid4().hex[:8]}@test.ru",
        "password": password,
        "last_name": "Петрова",
        "first_name": "Анна",
        "birth_date": "1995-05-20",
    }
    data.update(extra)
    return data


async def create_user(client, email=None, password="password123", **extra):
    data = user_data(email, password, **extra)
    email = data["email"]
    r = await client.post("/users", json=data)
    assert r.status_code == 201, r.text
    r = await client.post("/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def create_account(client, headers, currency="RUB", money=None):
    r = await client.post("/accounts", json={"currency": currency}, headers=headers)
    assert r.status_code == 201, r.text
    acc_id = r.json()["id"]
    if money:
        r = await client.post(f"/accounts/{acc_id}/deposit", json={"amount": money}, headers=headers)
        assert r.status_code == 200, r.text
    return acc_id


async def get_balance(client, headers, acc_id):
    r = await client.get(f"/accounts/{acc_id}", headers=headers)
    return r.json()["balance"]


def new_key():
    return uuid.uuid4().hex
