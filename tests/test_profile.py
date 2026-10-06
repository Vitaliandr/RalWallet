from datetime import date

from tests.conftest import create_account, create_user, get_balance, new_key, user_data


async def test_register_needs_name(client):
    r = await client.post("/users", json={"email": "x@test.ru", "password": "password123"})
    assert r.status_code == 422

    r = await client.post("/users", json=user_data(first_name="Анна1"))
    assert r.status_code == 422
    assert "Имя" in r.json()["detail"]


async def test_profile_fields(client):
    h = await create_user(client, phone="8 (999) 123-45-67", middle_name="  Сергеевна ")
    me = (await client.get("/users/me", headers=h)).json()
    assert me["phone"] == "+79991234567"
    assert me["middle_name"] == "Сергеевна"
    assert me["birth_date"] == "1995-05-20"

    r = await client.patch("/users/me", json={"first_name": "Мария", "phone": None}, headers=h)
    assert r.status_code == 200
    assert r.json()["first_name"] == "Мария"
    assert r.json()["phone"] is None
    # то что не присылали не трогаем
    assert r.json()["last_name"] == "Петрова"

    r = await client.patch("/users/me", json={"last_name": None}, headers=h)
    assert r.status_code == 400


async def test_phone_unique(client):
    await create_user(client, phone="+79990000001")
    r = await client.post("/users", json=user_data(phone="89990000001"))
    assert r.status_code == 409

    h = await create_user(client)
    r = await client.patch("/users/me", json={"phone": "9990000001"}, headers=h)
    assert r.status_code == 409


async def test_only_adults_open_accounts(client):
    today = date.today()
    h = await create_user(client, birth_date=f"{today.year - 17}-01-01")
    r = await client.post("/accounts", json={"currency": "RUB"}, headers=h)
    assert r.status_code == 400
    assert "18" in r.json()["detail"]


async def test_first_account_is_main(client):
    h = await create_user(client)
    a = await create_account(client, h)
    b = await create_account(client, h)
    assert (await client.get("/users/me", headers=h)).json()["main_account_id"] == a

    r = await client.patch("/users/me", json={"main_account_id": b}, headers=h)
    assert r.json()["main_account_id"] == b

    # чужой счёт основным не сделать
    h2 = await create_user(client)
    r = await client.patch("/users/me", json={"main_account_id": a}, headers=h2)
    assert r.status_code == 404


async def test_change_password_logs_out_everywhere(client):
    h_old = await create_user(client, "pass@test.ru")
    r = await client.post("/users/me/password",
                          json={"current_password": "password123", "new_password": "newpassword1"}, headers=h_old)
    assert r.status_code == 200
    h_new = {"Authorization": f"Bearer {r.json()['access_token']}"}

    #старый токен больше не работает а новый работает
    assert (await client.get("/users/me", headers=h_old)).status_code == 401
    assert (await client.get("/users/me", headers=h_new)).status_code == 200

    r = await client.post("/auth/login", json={"email": "pass@test.ru", "password": "password123"})
    assert r.status_code == 401
    r = await client.post("/auth/login", json={"email": "pass@test.ru", "password": "newpassword1"})
    assert r.status_code == 200


async def test_change_password_wrong_current(client):
    h = await create_user(client)
    r = await client.post("/users/me/password",
                          json={"current_password": "nope12345", "new_password": "newpassword1"}, headers=h)
    assert r.status_code == 400


async def test_change_email(client):
    await create_user(client, "busy@test.ru")
    h = await create_user(client, "old@test.ru")

    r = await client.post("/users/me/email", json={"new_email": "new@test.ru", "password": "wrong"}, headers=h)
    assert r.status_code == 400
    r = await client.post("/users/me/email", json={"new_email": "busy@test.ru", "password": "password123"}, headers=h)
    assert r.status_code == 409
    r = await client.post("/users/me/email", json={"new_email": "New@Test.ru", "password": "password123"}, headers=h)
    assert r.json()["email"] == "new@test.ru"

    r = await client.post("/auth/login", json={"email": "new@test.ru", "password": "password123"})
    assert r.status_code == 200


async def test_login_lock(client):
    await create_user(client, "lock@test.ru")
    for _ in range(5):
        r = await client.post("/auth/login", json={"email": "lock@test.ru", "password": "badpass1"})
        assert r.status_code == 401

    #даже правильный пароль не пускаем пока блок
    r = await client.post("/auth/login", json={"email": "lock@test.ru", "password": "password123"})
    assert r.status_code == 429
    assert "15 мин" in r.json()["detail"]


async def test_login_history(client):
    h = await create_user(client, "hist@test.ru")
    await client.post("/auth/login", json={"email": "hist@test.ru", "password": "badpass1"})

    r = await client.get("/users/me/logins", headers=h, )
    logins = r.json()
    # новые сверху
    assert [x["success"] for x in logins] == [False, True]


async def test_transfer_by_phone(client):
    h1 = await create_user(client)
    h2 = await create_user(client, phone="+79995554433", first_name="Пётр", last_name="Иванов")
    a = await create_account(client, h1, money="100")
    b_main = await create_account(client, h2)
    await create_account(client, h2)

    r = await client.post("/transfers/recipient", json={"phone": "8 999 555 44 33"}, headers=h1)
    assert r.json() == {"account_id": b_main, "currency": "RUB", "name": "Пётр И."}

    r = await client.post(
        "/transfers",
        json={"from_account_id": a, "to_phone": "89995554433", "amount": "30"},
        headers={**h1, "Idempotency-Key": new_key()},
    )
    assert r.status_code == 201
    assert r.json()["to_account_id"] == b_main
    assert await get_balance(client, h2, b_main) == "30.00"


async def test_transfer_by_unknown_phone(client):
    h = await create_user(client)
    a = await create_account(client, h, money="100")
    r = await client.post(
        "/transfers",
        json={"from_account_id": a, "to_phone": "+79990009999", "amount": "1"},
        headers={**h, "Idempotency-Key": new_key()},
    )
    assert r.status_code == 404


async def test_daily_limit(client):
    h1 = await create_user(client)
    h2 = await create_user(client)
    a = await create_account(client, h1, money="1000")
    a2 = await create_account(client, h1)
    b = await create_account(client, h2)
    await client.patch("/users/me", json={"daily_limit": "100"}, headers=h1)

    async def send(to, amount):
        return await client.post(
            "/transfers",
            json={"from_account_id": a, "to_account_id": to, "amount": amount},
            headers={**h1, "Idempotency-Key": new_key()},
        )

    assert (await send(b, "60")).status_code == 201
    r = await send(b, "50")
    assert r.status_code == 400
    assert "40.00" in r.json()["detail"]
    #себе на другой счёт лимит не ограничивает
    assert (await send(a2, "500")).status_code == 201
    assert (await send(b, "40")).status_code == 201


async def test_stats(client):
    h1 = await create_user(client)
    h2 = await create_user(client)
    a = await create_account(client, h1, money="1000")
    a2 = await create_account(client, h1)
    b = await create_account(client, h2, money="50")

    for frm, to, amount, h in [(a, b, "100", h1), (a, a2, "300", h1), (b, a, "20", h2)]:
        await client.post(
            "/transfers",
            json={"from_account_id": frm, "to_account_id": to, "amount": amount},
            headers={**h, "Idempotency-Key": new_key()},
        )

    s = (await client.get("/users/me/stats", headers=h1)).json()
    assert s["accounts_count"] == 2
    # перевод самому себе на 300 не считается
    assert s["month"] == [{"currency": "RUB", "sent": "100.00", "received": "20.00"}]
