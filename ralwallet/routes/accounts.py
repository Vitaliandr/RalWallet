from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ralwallet.api_models import AccountCreate, AccountOut, DepositIn, OperationOut, to_kopecks
from ralwallet.auth import current_user
from ralwallet.core import wallets as service
from ralwallet.database import get_session
from ralwallet.settings import settings
from ralwallet.tables import User

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.post("", response_model=AccountOut, status_code=201)
async def create_account(
    data: AccountCreate, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> AccountOut:
    acc = await service.create_account(session, user, data.currency)
    return AccountOut.from_model(acc)


@router.get("", response_model=list[AccountOut])
async def my_accounts(
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> list[AccountOut]:
    accounts = await service.get_user_accounts(session, user.id)
    return [AccountOut.from_model(a) for a in accounts]


@router.get("/{account_id}", response_model=AccountOut)
async def get_account(
    account_id: int, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> AccountOut:
    acc = await service.get_user_account(session, account_id, user.id)
    return AccountOut.from_model(acc)


# заглушка вместо платёжного шлюза
@router.post("/{account_id}/deposit", response_model=AccountOut)
async def deposit(
    account_id: int,
    data: DepositIn,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> AccountOut:
    #иначе пополняй себя на сколько хочешь
    if not settings.test_deposits:
        raise HTTPException(403, "тестовое пополнение отключено")
    if data.amount > Decimal(settings.max_deposit):
        raise HTTPException(400, f"за один раз можно пополнить не больше {settings.max_deposit:,}".replace(",", " "))
    acc = await service.deposit(session, account_id, user.id, to_kopecks(data.amount))
    return AccountOut.from_model(acc)


@router.get("/{account_id}/operations", response_model=list[OperationOut])
async def operations(
    account_id: int,
    limit: int = Query(50, ge=1, le=200),
    before_id: int | None = None,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[OperationOut]:
    ops = await service.get_operations(session, account_id, user.id, limit, before_id)
    return [OperationOut.from_model(op, t) for op, t in ops]
