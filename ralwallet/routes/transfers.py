import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from ralwallet.api_models import RecipientIn, RecipientOut, TransferIn, TransferOut
from ralwallet.auth import current_user
from ralwallet.core import transfer as service
from ralwallet.core.profile import find_recipient, short_name
from ralwallet.database import get_session
from ralwallet.limiter import get_redis, hit
from ralwallet.settings import settings
from ralwallet.tables import User

router = APIRouter(prefix="/transfers", tags=["transfers"])
log = logging.getLogger(__name__)


@router.post("", response_model=TransferOut, status_code=201)
async def create_transfer(
    data: TransferIn,
    response: Response,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=64),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> TransferOut:
    if not await hit(redis, f"transfers:{user.id}", settings.transfers_per_minute):
        log.warning("лимит переводов user=%s", user.id)
        raise HTTPException(429, "слишком много переводов, подождите минуту")

    transfer, created = await service.make_transfer(session, user.id, data, idempotency_key)
    if not created:
        # повтор, 200 вместо 201
        response.status_code = 200
    return TransferOut.from_model(transfer)


#кому уйдут деньги, до отправки
# POST чтоб телефоны не попадали в логи
@router.post("/recipient", response_model=RecipientOut)
async def recipient(
    data: RecipientIn,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> RecipientOut:
    #иначе можно перебрать все номера
    if not await hit(redis, f"lookup:{user.id}", settings.lookups_per_minute):
        #похоже на перебор телефонов
        log.warning("лимит поиска получателя user=%s", user.id)
        raise HTTPException(429, "слишком много запросов, подождите минуту")
    acc, owner = await find_recipient(session, data.account, data.phone)
    return RecipientOut(account_id=acc.id, currency=acc.currency, name=short_name(owner))


@router.get("/{transfer_id}", response_model=TransferOut)
async def get_transfer(
    transfer_id: int, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> TransferOut:
    t = await service.get_transfer(session, transfer_id, user.id)
    from_name, to_name = await service.party_names(session, t)
    return TransferOut.from_model(t, from_name, to_name)
