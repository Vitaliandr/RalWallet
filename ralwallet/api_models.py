import re
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Self

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

if TYPE_CHECKING:
    from ralwallet.tables import Account, Operation, Transfer, User

CURRENCIES = {"RUB", "USD", "EUR"}

NAME_RE = re.compile(r"^[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё' -]*$")


def clean_name(v: str | None) -> str | None:
    if v is None:
        return None
    v = " ".join(v.split())
    if not v:
        return None
    if len(v) > 50 or not NAME_RE.match(v):
        raise ValueError("только буквы, пробел и дефис")
    return v


def normalize_phone(v: str | None) -> str | None:
    #в базе всегда +79991234567
    if v is None:
        return None
    digits = re.sub(r"\D", "", v)
    if not digits:
        return None
    if len(digits) == 11 and digits[0] in "78":
        return "+7" + digits[1:]
    if len(digits) == 10 and digits[0] == "9":
        return "+7" + digits
    raise ValueError("нужен российский номер, например +7 999 123-45-67")


def check_password_bytes(v: str) -> str:
    # bcrypt берёт только 72 байта, а у кириллицы 2 байта на букву
    if len(v.encode()) > 72:
        raise ValueError("слишком длинный пароль (не больше 72 байт, кириллица считается за 2)")
    return v


def check_birth_date(v: date | None) -> date | None:
    if v is None:
        return None
    if v > date.today():
        raise ValueError("дата из будущего")
    if v.year < 1900:
        raise ValueError("проверьте год")
    return v


def to_kopecks(value: Decimal) -> int:
    return int(value * 100)


def from_kopecks(value: int) -> Decimal:
    # иначе 100 коп отдаётся как 1 а не 1.00
    return (Decimal(value) / 100).quantize(Decimal("0.01"))


class Money(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=15, decimal_places=2)


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    last_name: str
    first_name: str
    middle_name: str | None = None
    birth_date: date
    phone: str | None = None

    @field_validator("password")
    @classmethod
    def check_pass(cls, v: str) -> str:
        return check_password_bytes(v)

    @field_validator("last_name", "first_name", "middle_name")
    @classmethod
    def check_names(cls, v: str | None) -> str | None:
        return clean_name(v)

    @field_validator("phone")
    @classmethod
    def check_phone(cls, v: str | None) -> str | None:
        return normalize_phone(v)

    @field_validator("birth_date")
    @classmethod
    def check_birth(cls, v: date | None) -> date | None:
        return check_birth_date(v)

    @model_validator(mode="after")
    def names_not_empty(self) -> Self:
        #clean_name из пробелов делает None
        if not self.last_name or not self.first_name:
            raise ValueError("укажите фамилию и имя")
        return self


class ProfileUpdate(BaseModel):
    # PATCH, меняем только присланное
    last_name: str | None = None
    first_name: str | None = None
    middle_name: str | None = None
    phone: str | None = None
    birth_date: date | None = None
    main_account_id: int | None = None
    daily_limit: Decimal | None = Field(None, gt=0, max_digits=15, decimal_places=2)

    @field_validator("last_name", "first_name", "middle_name")
    @classmethod
    def check_names(cls, v: str | None) -> str | None:
        return clean_name(v)

    @field_validator("phone")
    @classmethod
    def check_phone(cls, v: str | None) -> str | None:
        return normalize_phone(v)

    @field_validator("birth_date")
    @classmethod
    def check_birth(cls, v: date | None) -> date | None:
        return check_birth_date(v)


class UserOut(BaseModel):
    id: int
    email: str
    last_name: str | None
    first_name: str | None
    middle_name: str | None
    phone: str | None
    birth_date: date | None
    main_account_id: int | None
    daily_limit: Decimal | None
    created_at: datetime

    @classmethod
    def from_model(cls, u: "User") -> Self:
        return cls(
            id=u.id,
            email=u.email,
            last_name=u.last_name,
            first_name=u.first_name,
            middle_name=u.middle_name,
            phone=u.phone,
            birth_date=u.birth_date,
            main_account_id=u.main_account_id,
            daily_limit=from_kopecks(u.daily_limit) if u.daily_limit is not None else None,
            created_at=u.created_at,
        )


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(min_length=8, max_length=72)

    @field_validator("new_password")
    @classmethod
    def check_pass(cls, v: str) -> str:
        return check_password_bytes(v)


class EmailChange(BaseModel):
    new_email: EmailStr
    password: str = Field(max_length=256)


class LoginEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    success: bool
    ip: str | None
    user_agent: str | None
    created_at: datetime


class MonthStat(BaseModel):
    currency: str
    sent: Decimal
    received: Decimal


class StatsOut(BaseModel):
    registered_at: datetime
    accounts_count: int
    month: list[MonthStat]


class RecipientIn(BaseModel):
    account: int | None = None
    phone: str | None = None

    @field_validator("phone")
    @classmethod
    def check_phone(cls, v: str | None) -> str | None:
        return normalize_phone(v)

    @model_validator(mode="after")
    def one_of(self) -> Self:
        if (self.account is None) == (self.phone is None):
            raise ValueError("укажите номер счёта или телефон")
        return self


class RecipientOut(BaseModel):
    account_id: int
    currency: str
    name: str | None  #типа "Анна П."


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=256)  # огромный пароль в bcrypt не пускаем


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class AccountCreate(BaseModel):
    currency: str = "RUB"

    @field_validator("currency")
    @classmethod
    def check_currency(cls, v: str) -> str:
        v = v.upper()
        if v not in CURRENCIES:
            raise ValueError(f"валюта должна быть одной из: {', '.join(sorted(CURRENCIES))}")
        return v


class AccountOut(BaseModel):
    id: int
    currency: str
    balance: Decimal
    created_at: datetime

    @classmethod
    def from_model(cls, acc: "Account") -> Self:
        return cls(id=acc.id, currency=acc.currency, balance=from_kopecks(acc.balance), created_at=acc.created_at)


class DepositIn(Money):
    pass


class TransferIn(Money):
    from_account_id: int
    to_account_id: int | None = None
    to_phone: str | None = None

    @field_validator("to_phone")
    @classmethod
    def check_phone(cls, v: str | None) -> str | None:
        return normalize_phone(v)

    @model_validator(mode="after")
    def one_recipient(self) -> Self:
        if (self.to_account_id is None) == (self.to_phone is None):
            raise ValueError("укажите номер счёта или телефон получателя")
        return self


class TransferOut(BaseModel):
    id: int
    from_account_id: int
    to_account_id: int
    amount: Decimal
    currency: str
    created_at: datetime
    #только для окна с подробностями
    from_name: str | None = None
    to_name: str | None = None

    @classmethod
    def from_model(cls, t: "Transfer", from_name: str | None = None, to_name: str | None = None) -> Self:
        return cls(
            id=t.id,
            from_account_id=t.from_account_id,
            to_account_id=t.to_account_id,
            amount=from_kopecks(t.amount),
            currency=t.currency,
            created_at=t.created_at,
            from_name=from_name,
            to_name=to_name,
        )


class OperationOut(BaseModel):
    id: int
    kind: str
    amount: Decimal
    balance_after: Decimal
    transfer_id: int | None
    other_account_id: int | None  # куда ушло или откуда пришло
    created_at: datetime

    @classmethod
    def from_model(cls, op: "Operation", transfer: "Transfer | None" = None) -> Self:
        other = None
        if transfer is not None:
            other = transfer.to_account_id if op.account_id == transfer.from_account_id else transfer.from_account_id
        return cls(
            id=op.id,
            kind=op.kind,
            amount=from_kopecks(op.amount),
            balance_after=from_kopecks(op.balance_after),
            transfer_id=op.transfer_id,
            other_account_id=other,
            created_at=op.created_at,
        )
