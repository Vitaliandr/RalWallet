import json
import logging

import pytest
from fakeredis import FakeAsyncRedis
from sqlalchemy.ext.asyncio import async_sessionmaker

from ralwallet.background import notify, outbox_sender
from tests.conftest import create_account, create_user, new_key

# кафки нет, вместо неё заглушка


class FakeProducer:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send_and_wait(self, topic, key, value):
        if self.fail:
            raise ConnectionError("кафка недоступна")
        self.sent.append((topic, key.decode(), json.loads(value)))


async def make_transfer(client):
    h1 = await create_user(client)
    h2 = await create_user(client)
    a = await create_account(client, h1, money="100")
    b = await create_account(client, h2)
    r = await client.post("/transfers", json={"from_account_id": a, "to_account_id": b, "amount": "25"},
                          headers={**h1, "Idempotency-Key": new_key()})
    return r.json()["id"]


async def test_relay_sends_and_marks(client, engine):
    tid = await make_transfer(client)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    producer = FakeProducer()

    assert await outbox_sender.publish_batch(producer, sessions) == 1
    topic, key, payload = producer.sent[0]
    assert key == str(tid)
    assert payload["type"] == "transfer.completed"
    assert payload["amount"] == "25.00"

    #второй раз отправлять нечего, событие помечено
    assert await outbox_sender.publish_batch(producer, sessions) == 0
    assert len(producer.sent) == 1


async def test_relay_keeps_event_if_kafka_down(client, engine):
    await make_transfer(client)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    with pytest.raises(ConnectionError):
        await outbox_sender.publish_batch(FakeProducer(fail=True), sessions)

    # кафка ожила, событие не потерялось (at-least-once)
    producer = FakeProducer()
    assert await outbox_sender.publish_batch(producer, sessions) == 1


def event(**extra):
    e = {"event_id": "e1", "type": "transfer.completed", "amount": "10.00", "currency": "RUB",
         "from_user_id": 1, "to_user_id": 2, "from_account_id": 111, "to_account_id": 222}
    e.update(extra)
    return e


async def test_notifier_skips_duplicates(caplog):
    caplog.set_level(logging.INFO)
    redis = FakeAsyncRedis(decode_responses=True)

    assert await notify.process(redis, event()) is True
    #relay может прислать то же событие ещё раз
    assert await notify.process(redis, event()) is False
    assert caplog.text.count("[уведомление]") == 2  # отправителю и получателю, один раз


async def test_notifier_retries_after_error(monkeypatch):
    redis = FakeAsyncRedis(decode_responses=True)

    async def broken(e):
        raise RuntimeError("упала отправка")
    monkeypatch.setattr(notify, "handle", broken)
    with pytest.raises(RuntimeError):
        await notify.process(redis, event(event_id="e2"))

    #отметка снята, второй раз обработается
    monkeypatch.undo()
    assert await notify.process(redis, event(event_id="e2")) is True


async def test_notifier_own_transfer_one_message(caplog):
    caplog.set_level(logging.INFO)
    await notify.process(FakeAsyncRedis(), event(event_id="e3", to_user_id=1))
    assert caplog.text.count("[уведомление]") == 1
