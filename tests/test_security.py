import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from pydantic import ValidationError

from ralwallet.settings import Settings, settings
from tests.conftest import create_account, create_user, user_data


def forged(secret, **claims):
    payload = {"sub": "1", "ver": 0, "jti": "x" * 32, "exp": datetime.now(UTC) + timedelta(hours=1)}
    payload.update(claims)
    return {"Authorization": "Bearer " + jwt.encode(payload, secret, algorithm="HS256")}


def test_weak_secret_does_not_start():
    for bad in ["", "dev-secret", "change-me", "short"]:
        with pytest.raises(ValidationError):
            Settings(jwt_secret=bad)


async def test_forged_tokens(client):
    await create_user(client)
    #известный секрет из старого docker-compose
    assert (await client.get("/users/me", headers=forged("dev-secret"))).status_code == 401

    # alg=none
    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    none_token = b64({"alg": "none", "typ": "JWT"}) + "." + b64({"sub": "1", "ver": 0, "jti": "a"}) + "."
    r = await client.get("/users/me", headers={"Authorization": "Bearer " + none_token})
    assert r.status_code == 401

    #без jti/exp не принимаем
    no_exp = jwt.encode({"sub": "1", "ver": 0, "jti": "a"}, settings.jwt_secret, algorithm="HS256")
    r = await client.get("/users/me", headers={"Authorization": "Bearer " + no_exp})
    assert r.status_code == 401

    # мусор в sub не должен ронять сервер в 500
    assert (await client.get("/users/me", headers=forged(settings.jwt_secret, sub="abc"))).status_code == 401


async def test_logout_revokes_token(client):
    h1 = await create_user(client, "out@test.ru")
    r = await client.post("/auth/login", json={"email": "out@test.ru", "password": "password123"})
    h2 = {"Authorization": f"Bearer {r.json()['access_token']}"}

    assert (await client.post("/auth/logout", headers=h1)).status_code == 204
    assert (await client.get("/users/me", headers=h1)).status_code == 401
    # выход с одного устройства не трогает другое
    assert (await client.get("/users/me", headers=h2)).status_code == 200


async def test_parallel_password_bruteforce(client):
    await create_user(client, "victim@test.ru")
    tries = [
        client.post("/auth/login", json={"email": "victim@test.ru", "password": f"guess{i}xxx"})
        for i in range(20)
    ]
    codes = [r.status_code for r in await asyncio.gather(*tries)]
    # пароль проверили максимум 5 раз
    assert codes.count(401) <= 5
    assert codes.count(429) >= 15


async def test_password_bruteforce_via_change_password(client):
    h = await create_user(client)
    for _ in range(5):
        r = await client.post("/users/me/password",
                              json={"current_password": "wrong1234", "new_password": "newpass123"}, headers=h)
        assert r.status_code == 400
    r = await client.post("/users/me/password",
                          json={"current_password": "password123", "new_password": "newpass123"}, headers=h)
    assert r.status_code == 429


async def test_bcrypt_72_bytes(client):
    #40 кириллических букв = 80 байт, bcrypt бы отрезал хвост
    r = await client.post("/users", json=user_data(password="п" * 40 + "a"))
    assert r.status_code == 422


async def test_deposit_limit(client):
    h = await create_user(client)
    acc = await create_account(client, h)
    r = await client.post(f"/accounts/{acc}/deposit", json={"amount": "9999999999999.99"}, headers=h)
    assert r.status_code == 400


async def test_deposit_can_be_disabled(client, monkeypatch):
    h = await create_user(client)
    acc = await create_account(client, h)
    monkeypatch.setattr(settings, "test_deposits", False)
    r = await client.post(f"/accounts/{acc}/deposit", json={"amount": "10"}, headers=h)
    assert r.status_code == 403


async def test_phone_enumeration_limited(client):
    h = await create_user(client)
    codes = [(await client.post("/transfers/recipient", json={"phone": f"+7999000{i:04d}"}, headers=h)).status_code
             for i in range(settings.lookups_per_minute + 3)]
    assert codes[-1] == 429


async def test_injections_dont_break_anything(client):
    h = await create_user(client)
    acc = await create_account(client, h, money="100")
    attacks = [
        client.post("/auth/login", json={"email": "' OR 1=1 --", "password": "x"}),
        client.post("/auth/login", json={"email": "o'neil@test.ru", "password": "' OR '1'='1"}),
        client.get("/accounts/1 OR 1=1", headers=h),
        client.get(f"/accounts/{acc}/operations?before_id=1;DROP TABLE users", headers=h),
        client.post("/transfers/recipient", json={"phone": "+7'; DROP TABLE users;--"}, headers=h),
        client.patch("/users/me", json={"first_name": "<script>alert(1)</script>"}, headers=h),
        client.patch("/users/me", json={"last_name": "Robert'); DROP TABLE users;--"}, headers=h),
    ]
    for r in await asyncio.gather(*attacks):
        assert r.status_code in (401, 404, 422), (r.request.url, r.status_code)
    #база жива и деньги на месте
    assert (await client.get(f"/accounts/{acc}", headers=h)).json()["balance"] == "100.00"


async def test_mass_assignment(client):
    h = await create_user(client, "mass@test.ru")
    await client.patch("/users/me", json={"id": 1, "email": "x@x.ru", "token_version": 50, "password_hash": "x"},
                       headers=h)
    me = (await client.get("/users/me", headers=h)).json()
    assert me["email"] == "mass@test.ru"
    #token_version не тронут
    assert (await client.get("/users/me", headers=h)).status_code == 200


async def test_security_headers(client):
    r = await client.get("/")
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
