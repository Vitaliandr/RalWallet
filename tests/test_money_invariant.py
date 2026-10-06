import asyncio
import random

from sqlalchemy import text

from tests.conftest import create_account, create_user, new_key

# главный тест: сколько денег было столько и осталось


async def total_money(engine):
    async with engine.connect() as conn:
        return await conn.scalar(text("SELECT coalesce(sum(balance), 0) FROM accounts"))


async def test_money_does_not_appear_or_vanish(client, engine):
    rnd = random.Random(42)  # seed фиксированный, чтоб повторить падение

    users = []
    for i in range(4):
        phone = f"+7999000000{i}"
        h = await create_user(client, phone=phone)
        accs = [await create_account(client, h, money="1000") for _ in range(2)]
        users.append({"h": h, "accs": accs, "phone": phone})

    before = await total_money(engine)
    assert before == 4 * 2 * 1000 * 100  # в копейках

    async def random_transfer():
        sender = rnd.choice(users)
        receiver = rnd.choice(users)
        body = {"from_account_id": rnd.choice(sender["accs"]), "amount": str(rnd.randint(1, 400))}
        if rnd.random() < 0.3:
            body["to_phone"] = receiver["phone"]
        else:
            body["to_account_id"] = rnd.choice(receiver["accs"])
        key = new_key()
        r = await client.post("/transfers", json=body, headers={**sender["h"], "Idempotency-Key": key})
        #иногда шлём дубль
        if rnd.random() < 0.2:
            await client.post("/transfers", json=body, headers={**sender["h"], "Idempotency-Key": key})
        return r.status_code

    codes = await asyncio.gather(*[random_transfer() for _ in range(60)])

    assert codes.count(201) > 10
    assert set(codes) <= {201, 200, 400, 422}

    assert await total_money(engine) == before

    async with engine.connect() as conn:
        # баланс каждого счёта = сумма всех операций по нему
        broken = (await conn.execute(text("""
            SELECT a.id, a.balance, coalesce(sum(o.amount), 0) AS ops
            FROM accounts a LEFT JOIN operations o ON o.account_id = a.id
            GROUP BY a.id HAVING a.balance <> coalesce(sum(o.amount), 0)
        """))).all()
        assert broken == []

        # и никто не ушёл в минус
        negative = await conn.scalar(text("SELECT count(*) FROM accounts WHERE balance < 0"))
        assert negative == 0
