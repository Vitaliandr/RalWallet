from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ralwallet.database import Base

# все суммы в копейках, никаких float


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(100))
    #растёт при смене пароля, старые токены отваливаются
    token_version: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")

    last_name: Mapped[str | None] = mapped_column(String(50))
    first_name: Mapped[str | None] = mapped_column(String(50))
    middle_name: Mapped[str | None] = mapped_column(String(50))
    phone: Mapped[str | None] = mapped_column(String(12), unique=True)  # +79991234567
    birth_date: Mapped[date | None] = mapped_column(Date)

    # use_alter: users и accounts ссылаются друг на друга
    main_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", use_alter=True, name="fk_users_main_account", ondelete="SET NULL", onupdate="CASCADE")
    )
    daily_limit: Mapped[int | None] = mapped_column(BigInteger)  # в копейках, None = без лимита
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LoginEvent(Base):
    #история входов, удачные и неудачные
    __tablename__ = "logins"
    __table_args__ = (Index("ix_logins_user_id_id", "user_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    success: Mapped[bool] = mapped_column(Boolean)
    ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        # на всякий случай
        CheckConstraint("balance >= 0", name="balance_not_negative"),
    )

    #номер счёта = id, случайный
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    currency: Mapped[str] = mapped_column(String(3))
    balance: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    # для оптимистичной блокировки
    version: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Transfer(Base):
    __tablename__ = "transfers"
    __table_args__ = (
        #один ключ от одного юзера = один перевод
        UniqueConstraint("user_id", "idempotency_key", name="uq_transfer_idem_key"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("from_account_id <> to_account_id", name="different_accounts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    from_account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", onupdate="CASCADE"))
    to_account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", onupdate="CASCADE"))
    amount: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    idempotency_key: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Operation(Base):
    #amount со знаком, плюс пришло минус ушло
    __tablename__ = "operations"
    __table_args__ = (Index("ix_operations_account_id_id", "account_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", onupdate="CASCADE"))
    transfer_id: Mapped[int | None] = mapped_column(ForeignKey("transfers.id"))
    kind: Mapped[str] = mapped_column(String(20))  # deposit / transfer_in / transfer_out
    amount: Mapped[int] = mapped_column(BigInteger)
    balance_after: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OutboxEvent(Base):
    __tablename__ = "outbox"
    __table_args__ = (
        # частичный индекс, relay ищет только неотправленные
        Index("ix_outbox_not_sent", "id", postgresql_where=text("sent_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    topic: Mapped[str] = mapped_column(String(100))
    key: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
