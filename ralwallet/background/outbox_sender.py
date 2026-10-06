# relay: outbox -> kafka
# запуск: python -m ralwallet.background.outbox_sender
#at-least-once, дубли возможны
import asyncio
import json
import logging

from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaConnectionError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ralwallet import logs
from ralwallet.database import SessionLocal
from ralwallet.repositories import outbox as outbox_repo
from ralwallet.settings import settings

log = logging.getLogger("relay")


# в тестах подменяем и то и другое
async def publish_batch(
    producer: AIOKafkaProducer, make_session: async_sessionmaker[AsyncSession] = SessionLocal
) -> int:
    async with make_session() as session:
        events = await outbox_repo.take_unsent(session, settings.outbox_batch_size)

        for e in events:
            await producer.send_and_wait(
                e.topic,
                key=e.key.encode(),
                value=json.dumps(e.payload, ensure_ascii=False).encode(),
            )
            outbox_repo.mark_sent(e)

        await session.commit()
        return len(events)


async def start_producer() -> AIOKafkaProducer:
    #кафка может стартануть позже нас, ретраим
    while True:
        producer = AIOKafkaProducer(bootstrap_servers=settings.kafka_servers, acks="all")
        try:
            await producer.start()
            return producer
        except KafkaConnectionError:
            await producer.stop()
            log.warning("kafka недоступна, пробую ещё раз через 3 сек")
            await asyncio.sleep(3)


async def main() -> None:
    producer = await start_producer()
    log.info("relay запущен")
    try:
        while True:
            try:
                sent = await publish_batch(producer)
            except Exception:
                log.exception("ошибка при отправке пачки, повторим позже")
                sent = 0

            if sent:
                log.info("отправлено событий: %s", sent)
            else:
                await asyncio.sleep(settings.outbox_poll_interval)
    finally:
        await producer.stop()


if __name__ == "__main__":
    logs.setup(as_json=settings.log_json)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
