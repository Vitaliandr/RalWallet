from tests.conftest import create_user, user_data


async def test_register_and_me(client):
    headers = await create_user(client, "Ivan@Test.ru")
    r = await client.get("/users/me", headers=headers)
    assert r.status_code == 200
    assert r.json()["email"] == "ivan@test.ru"


async def test_duplicate_email(client):
    await create_user(client, "a@test.ru")
    r = await client.post("/users", json=user_data("a@test.ru"))
    assert r.status_code == 409


async def test_wrong_password(client):
    await create_user(client, "b@test.ru")
    r = await client.post("/auth/login", json={"email": "b@test.ru", "password": "wrongpass"})
    assert r.status_code == 401


async def test_no_token(client):
    r = await client.get("/users/me")
    assert r.status_code == 401

    r = await client.get("/users/me", headers={"Authorization": "Bearer abc"})
    assert r.status_code == 401
