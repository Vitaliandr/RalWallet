import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ralwallet.api_models import TransferIn, from_kopecks, to_kopecks
from ralwallet.core.profile import find_recipient, short_name
from ralwallet.errors import AppError, Conflict, NotEnoughMoney, NotFound
from ralwallet.repositories import accounts as accounts_repo
from ralwallet.repositories import operations as operations_repo
from ralwallet.repositories import outbox as outbox_repo
from ralwallet.repositories import transfers as transfers_repo
from ralwallet.repositories import users as users_repo
from ralwallet.settings import settings
from ralwallet.tables import Account, Operation, Transfer

log = logging.getLogger(__name__)


def _check_same_request(t: Transfer, from_id: int, to_id: int, amount: int) -> None:
    # ключ тот же а параметры другие, косяк клиента
    if (t.from_account_id, t.to_account_id, t.amount) != (from_id, to_id, amount):
        raise Conflict("Idempotency-Key уже использован для другого перевода")


async def make_transfer(
    session: AsyncSession, user_id: int, data: TransferIn, idem_key: str
) -> tuple[Transfer, bool]:
    #(перевод, created), created=False если повтор
    amount = to_kopecks(data.amount)
    from_id = data.from_account_id

    # телефон превращаем в счёт
    recipient, _ = await find_recipient(session, data.to_account_id, data.to_phone)
    to_id = recipient.id

    if from_id == to_id:
        raise AppError("нельзя перевести на тот же счёт")

    #1. может уже делали такой перевод
    old = await transfers_repo.by_key(session, user_id, idem_key)
    if old:
        _check_same_request(old, from_id, to_id, amount)
        log.info("повтор перевода %s по тому же Idempotency-Key", old.id)
        return old, False

    # 2. лочим отправителя, из-за суточного лимита
    sender = await users_repo.lock(session, user_id)

    #потом счета, ВСЕГДА по возрастанию id
    # иначе встречные A->B и B->A ловят дедлок
    locked: dict[int, Account | None] = {}
    for acc_id in sorted([from_id, to_id]):
        locked[acc_id] = await accounts_repo.lock(session, acc_id)

    src = locked[from_id]
    dst = locked[to_id]

    if src is None or src.user_id != user_id:
        raise NotFound("счёт списания не найден")
    if dst is None:
        raise NotFound("счёт зачисления не найден")
    if src.currency != dst.currency:
        raise AppError("валюты счетов не совпадают")

    #3. ещё раз ключ, уже под локом (одновременные дубли)
    old = await transfers_repo.by_key(session, user_id, idem_key)
    if old:
        # commit а не rollback, rollback сбросит old
        await session.commit()
        _check_same_request(old, from_id, to_id, amount)
        return old, False

    if src.balance < amount:
        log.info("перевод отклонён, не хватает денег: счёт %s, нужно %s коп", src.id, amount)
        raise NotEnoughMoney("недостаточно средств")

    if sender.daily_limit is not None and dst.user_id != user_id:
        #сутки по UTC, пойдёт
        day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        already = await transfers_repo.sent_to_others(session, user_id, src.currency, day_start)
        if already + amount > sender.daily_limit:
            left = max(sender.daily_limit - already, 0)
            log.info("перевод отклонён по суточному лимиту user=%s", user_id)
            raise AppError(f"превышен суточный лимит, сегодня можно перевести ещё {from_kopecks(left)}")

    src.balance -= amount
    dst.balance += amount
    # version тоже, а то deposit не заметит
    src.version += 1
    dst.version += 1

    transfer = Transfer(
        user_id=user_id,
        from_account_id=src.id,
        to_account_id=dst.id,
        amount=amount,
        currency=src.currency,
        idempotency_key=idem_key,
    )
    await transfers_repo.add(session, transfer)

    operations_repo.add(
        session,
        Operation(account_id=src.id, transfer_id=transfer.id, kind="transfer_out",
                  amount=-amount, balance_after=src.balance),
        Operation(account_id=dst.id, transfer_id=transfer.id, kind="transfer_in",
                  amount=amount, balance_after=dst.balance),
    )

    #outbox: событие в той же транзакции что и перевод
    # так событие не потеряется, в кафку его отправит relay
    outbox_repo.add(session, settings.kafka_topic, str(transfer.id), {
        "event_id": str(uuid.uuid4()),
        "type": "transfer.completed",
        "transfer_id": transfer.id,
        "from_account_id": src.id,
        "to_account_id": dst.id,
        "from_user_id": src.user_id,
        "to_user_id": dst.user_id,
        "amount": str(from_kopecks(amount)),
        "currency": src.currency,
    })

    try:
        await session.commit()
    except IntegrityError:
        #дубли разошлись по разным счетам, спас unique
        await session.rollback()
        old = await transfers_repo.by_key(session, user_id, idem_key)
        if old is None:
            raise
        _check_same_request(old, from_id, to_id, amount)
        return old, False

    await session.refresh(transfer)
    log.info("перевод %s: %s -> %s, %s коп", transfer.id, src.id, dst.id, amount)
    return transfer, True


async def get_transfer(session: AsyncSession, transfer_id: int, user_id: int) -> Transfer:
    t = await transfers_repo.visible_to(session, transfer_id, user_id)
    if t is None:
        raise NotFound("перевод не найден")
    return t


async def party_names(session: AsyncSession, t: Transfer) -> tuple[str | None, str | None]:
    # оба имени типа "Анна П."
    names: list[str | None] = []
    for acc_id in (t.from_account_id, t.to_account_id):
        acc = await accounts_repo.get(session, acc_id)
        owner = await users_repo.get(session, acc.user_id) if acc else None
        names.append(short_name(owner) if owner else None)
    return names[0], names[1]
