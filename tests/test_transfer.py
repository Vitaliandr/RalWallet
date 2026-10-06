import asyncio

from sqlalchemy import func, select

from ralwallet.settings import settings
from ralwallet.tables import OutboxEvent, Transfer
from tests.conftest import create_account, create_user, get_balance, new_key


async def transfer(client, headers, src, dst, amount, key=None):
    return await client.post(
        "/transfers",
        json={"from_account_id": src, "to_account_id": dst, "amount": amount},
        headers={**headers, "Idempotency-Key": key or new_key()},
    )


async def test_simple_transfer(client):
    h1 = await create_user(client)
    h2 = await create_user(client, first_name="Пётр", last_name="Иванов")
    a = await create_account(client, h1, money="100")
    b = await create_account(client, h2)

    r = await transfer(client, h1, a, b, "30.25")
    assert r.status_code == 201
    assert r.json()["amount"] == "30.25"

    assert await get_balance(client, h1, a) == "69.75"
    assert await get_balance(client, h2, b) == "30.25"

    # получатель тоже видит, с именами
    tid = r.json()["id"]
    r = await client.get(f"/transfers/{tid}", headers=h2)
    assert r.status_code == 200
    assert (r.json()["from_name"], r.json()["to_name"]) == ("Анна П.", "Пётр И.")

    # а посторонний нет
    h3 = await create_user(client)
    r = await client.get(f"/transfers/{tid}", headers=h3)
    assert r.status_code == 404


async def test_not_enough_money(client):
    h = await create_user(client)
    a = await create_account(client, h, money="10")
    b = await create_account(client, h)

    r = await transfer(client, h, a, b, "10.01")
    assert r.status_code == 422
    assert await get_balance(client, h, a) == "10.00"


async def test_cant_transfer_from_other_account(client):
    h1 = await create_user(client)
    h2 = await create_user(client)
    a = await create_account(client, h1, money="100")
    b = await create_account(client, h2)

    r = await transfer(client, h2, a, b, "50")
    assert r.status_code == 404
    assert await get_balance(client, h1, a) == "100.00"


async def test_different_currency(client):
    h = await create_user(client)
    a = await create_account(client, h, "RUB", money="100")
    b = await create_account(client, h, "USD")
    r = await transfer(client, h, a, b, "1")
    assert r.status_code == 400


async def test_same_account(client):
    h = await create_user(client)
    a = await create_account(client, h, money="100")
    r = await transfer(client, h, a, a, "1")
    assert r.status_code == 400


async def test_need_idempotency_key(client):
    h = await create_user(client)
    a = await create_account(client, h, money="100")
    b = await create_account(client, h)
    r = await client.post("/transfers", json={"from_account_id": a, "to_account_id": b, "amount": "1"}, headers=h)
    assert r.status_code == 422


async def test_repeat_with_same_key(client):
    h = await create_user(client)
    a = await create_account(client, h, money="100")
    b = await create_account(client, h)
    key = new_key()

    r1 = await transfer(client, h, a, b, "40", key)
    r2 = await transfer(client, h, a, b, "40", key)

    assert r1.status_code == 201
    assert r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]
    # списали только один раз
    assert await get_balance(client, h, a) == "60.00"


async def test_same_key_other_params(client):
    h = await create_user(client)
    a = await create_account(client, h, money="100")
    b = await create_account(client, h)
    key = new_key()

    await transfer(client, h, a, b, "40", key)
    r = await transfer(client, h, a, b, "41", key)
    assert r.status_code == 409


async def test_parallel_same_key(client, engine):
    #клиент ретраит по таймауту
    h = await create_user(client)
    a = await create_account(client, h, money="100")
    b = await create_account(client, h)
    key = new_key()

    responses = await asyncio.gather(*[transfer(client, h, a, b, "10", key) for _ in range(10)])

    assert all(r.status_code in (200, 201) for r in responses)
    assert len({r.json()["id"] for r in responses}) == 1
    assert await get_balance(client, h, a) == "90.00"

    async with engine.connect() as conn:
        count = await conn.scalar(select(func.count()).select_from(Transfer))
    assert count == 1


async def test_parallel_transfers_no_overdraft(client):
    # 10 переводов по 100 при балансе 500,пройти должны ровно 5
    h = await create_user(client)
    a = await create_account(client, h, money="500")
    b = await create_account(client, h)

    responses = await asyncio.gather(*[transfer(client, h, a, b, "100") for _ in range(10)])
    codes = sorted(r.status_code for r in responses)

    assert codes.count(201) == 5
    assert codes.count(422) == 5
    assert await get_balance(client, h, a) == "0.00"
    assert await get_balance(client, h, b) == "500.00"


async def test_counter_transfers_no_deadlock(client):
    # встречные, без сортировки был бы дедлок
    h = await create_user(client)
    a = await create_account(client, h, money="1000")
    b = await create_account(client, h, money="1000")

    tasks = []
    for _ in range(10):
        tasks.append(transfer(client, h, a, b, "10"))
        tasks.append(transfer(client, h, b, a, "10"))
    responses = await asyncio.gather(*tasks)

    assert all(r.status_code == 201 for r in responses)
    assert await get_balance(client, h, a) == "1000.00"
    assert await get_balance(client, h, b) == "1000.00"


async def test_outbox_event_written(client, engine):
    h1 = await create_user(client)
    h2 = await create_user(client)
    a = await create_account(client, h1, money="100")
    b = await create_account(client, h2)

    r = await transfer(client, h1, a, b, "15")
    transfer_id = r.json()["id"]

    async with engine.connect() as conn:
        rows = (await conn.execute(select(OutboxEvent))).all()

    assert len(rows) == 1
    event = rows[0]
    assert event.sent_at is None
    assert event.payload["transfer_id"] == transfer_id
    assert event.payload["amount"] == "15.00"
    assert event.payload["type"] == "transfer.completed"


async def test_rate_limit(client, monkeypatch):
    monkeypatch.setattr(settings, "transfers_per_minute", 3)
    h = await create_user(client)
    a = await create_account(client, h, money="100")
    b = await create_account(client, h)

    codes = [(await transfer(client, h, a, b, "1")).status_code for _ in range(5)]
    assert codes == [201, 201, 201, 429, 429]
