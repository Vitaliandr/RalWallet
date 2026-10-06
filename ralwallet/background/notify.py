# consumer уведомлений
#запуск: python -m ralwallet.background.notify
#отправки пока нет, просто пишем в лог
import asyncio
import json
import logging
from typing import Any

from aiokafka import AIOKafkaConsumer
from aiokafka.errors import KafkaConnectionError
from redis.asyncio import Redis

from ralwallet import logs
from ralwallet.limiter import get_redis
from ralwallet.settings import settings

log = logging.getLogger("notifier")

GROUP_ID = "notifier"
# неделя
DEDUP_TTL = 60 * 60 * 24 * 7


async def send_notification(user_id: int, text: str) -> None:
    # TODO: прикрутить реальную отправку
    log.info("[уведомление] user=%s: %s", user_id, text)


async def handle(event: dict[str, Any]) -> None:
    if event.get("type") != "transfer.completed":
        return

    amount = f"{event['amount']} {event['currency']}"
    await send_notification(event["from_user_id"], f"Перевод {amount} со счёта {event['from_account_id']} выполнен")
    #себе переводил, второе не нужно
    if event["to_user_id"] != event["from_user_id"]:
        await send_notification(event["to_user_id"], f"Вам пришло {amount} на счёт {event['to_account_id']}")


async def start_consumer() -> AIOKafkaConsumer:
    while True:
        consumer = AIOKafkaConsumer(
            settings.kafka_topic,
            bootstrap_servers=settings.kafka_servers,
            group_id=GROUP_ID,
            enable_auto_commit=False,  # коммитим сами
            auto_offset_reset="earliest",
        )
        try:
            await consumer.start()
            return consumer
        except KafkaConnectionError:
            await consumer.stop()
            log.warning("kafka недоступна, пробую ещё раз через 3 сек")
            await asyncio.sleep(3)


async def process(redis: Redis, event: dict[str, Any]) -> bool:
    #вынес из цикла для тестов. False = дубль
    dedup_key = f"notified:{event['event_id']}"

    # SET NX, уже есть значит дубль
    is_new = await redis.set(dedup_key, 1, nx=True, ex=DEDUP_TTL)
    if not is_new:
        log.info("дубль события %s, пропускаю", event["event_id"])
        return False
    try:
        await handle(event)
    except Exception:
        #чтоб при повторе попробовать ещё раз
        await redis.delete(dedup_key)
        raise
    return True


async def main() -> None:
    redis = get_redis()
    consumer = await start_consumer()
    log.info("notifier запущен")
    try:
        async for msg in consumer:
            await process(redis, json.loads(msg.value))
            await consumer.commit()
    finally:
        await consumer.stop()


if __name__ == "__main__":
    logs.setup(as_json=settings.log_json)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
