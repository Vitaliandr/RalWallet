import logging

from ralwallet.logs import mask_email
from tests.conftest import create_user


async def test_request_id_header(client):
    r = await client.get("/health")
    assert len(r.headers["x-request-id"]) == 12

    # свой id сохраняется
    r = await client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert r.headers["x-request-id"] == "abc-123"

    #а мусор заменяется
    r = await client.get("/health", headers={"X-Request-ID": "x\\nFAKE LOG LINE"})
    assert "FAKE" not in r.headers["x-request-id"]


def test_mask_email():
    assert mask_email("anna@mail.ru") == "a***@mail.ru"
    assert mask_email("bad") == "***"


async def test_no_personal_data_in_logs(client, caplog):
    caplog.set_level(logging.INFO)
    await create_user(client, "secret.person@test.ru", phone="+79990001122")
    await client.post("/auth/login", json={"email": "secret.person@test.ru", "password": "wrongpass1"})

    text = caplog.text
    assert "неудачный вход s***@test.ru" in text
    # ни полного email, ни телефона, ни паролей
    for leak in ["secret.person", "79990001122", "wrongpass1", "password123"]:
        assert leak not in text
