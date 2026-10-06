import asyncio

from tests.conftest import create_account, create_user, get_balance


async def test_account_numbers(client):
    h = await create_user(client)
    ids = [await create_account(client, h) for _ in range(5)]
    assert all(len(str(i)) == 10 for i in ids)
    # не подряд
    assert sorted(ids) != list(range(min(ids), min(ids) + 5))

    # список в порядке открытия а не по номеру
    r = await client.get("/accounts", headers=h)
    assert [a["id"] for a in r.json()] == ids


async def test_create_and_deposit(client):
    h = await create_user(client)
    acc = await create_account(client, h, money="100.50")
    assert await get_balance(client, h, acc) == "100.50"


async def test_no_float_problems(client):
    # во float 0.1 + 0.2 != 0.3
    h = await create_user(client)
    acc = await create_account(client, h)
    await client.post(f"/accounts/{acc}/deposit", json={"amount": "0.1"}, headers=h)
    await client.post(f"/accounts/{acc}/deposit", json={"amount": "0.2"}, headers=h)
    assert await get_balance(client, h, acc) == "0.30"


async def test_bad_amounts(client):
    h = await create_user(client)
    acc = await create_account(client, h)
    for amount in ["0", "-5", "1.001", "abc"]:
        r = await client.post(f"/accounts/{acc}/deposit", json={"amount": amount}, headers=h)
        assert r.status_code == 422, amount


async def test_bad_currency(client):
    h = await create_user(client)
    r = await client.post("/accounts", json={"currency": "BTC"}, headers=h)
    assert r.status_code == 422


async def test_cant_see_other_account(client):
    h1 = await create_user(client)
    h2 = await create_user(client)
    acc = await create_account(client, h1, money="10")

    r = await client.get(f"/accounts/{acc}", headers=h2)
    assert r.status_code == 404
    r = await client.post(f"/accounts/{acc}/deposit", json={"amount": "10"}, headers=h2)
    assert r.status_code == 404


async def test_parallel_deposits(client):
    #ни одно пополнение не потерялось
    h = await create_user(client)
    acc = await create_account(client, h)

    tasks = [client.post(f"/accounts/{acc}/deposit", json={"amount": "1"}, headers=h) for _ in range(5)]
    responses = await asyncio.gather(*tasks)

    assert all(r.status_code == 200 for r in responses)
    assert await get_balance(client, h, acc) == "5.00"


async def test_operations_history(client):
    h = await create_user(client)
    acc = await create_account(client, h)
    for i in range(1, 6):
        await client.post(f"/accounts/{acc}/deposit", json={"amount": str(i)}, headers=h)

    r = await client.get(f"/accounts/{acc}/operations", params={"limit": 3}, headers=h)
    ops = r.json()
    assert [op["amount"] for op in ops] == ["5.00", "4.00", "3.00"]
    assert ops[0]["balance_after"] == "15.00"

    # следующая страница
    r = await client.get(f"/accounts/{acc}/operations", params={"limit": 3, "before_id": ops[-1]["id"]}, headers=h)
    assert [op["amount"] for op in r.json()] == ["2.00", "1.00"]
