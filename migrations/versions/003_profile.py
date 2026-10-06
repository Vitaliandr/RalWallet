"""профиль: фио, телефон, дата рождения, основной счёт, лимит, история входов

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-03
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("token_version", sa.BigInteger(), server_default="0", nullable=False))
    op.add_column("users", sa.Column("last_name", sa.String(50), nullable=True))
    op.add_column("users", sa.Column("first_name", sa.String(50), nullable=True))
    op.add_column("users", sa.Column("middle_name", sa.String(50), nullable=True))
    op.add_column("users", sa.Column("phone", sa.String(12), nullable=True))
    op.add_column("users", sa.Column("birth_date", sa.Date(), nullable=True))
    op.add_column("users", sa.Column("main_account_id", sa.BigInteger(), nullable=True))
    op.add_column("users", sa.Column("daily_limit", sa.BigInteger(), nullable=True))
    op.create_unique_constraint("users_phone_key", "users", ["phone"])
    op.create_foreign_key(
        "fk_users_main_account", "users", "accounts", ["main_account_id"], ["id"],
        ondelete="SET NULL", onupdate="CASCADE",
    )

    # основным делаем первый счёт
    op.execute("""
        UPDATE users u SET main_account_id = (
            SELECT a.id FROM accounts a WHERE a.user_id = u.id ORDER BY a.created_at LIMIT 1
        )
    """)

    op.create_table(
        "logins",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_logins_user_id_id", "logins", ["user_id", "id"])


def downgrade():
    op.drop_table("logins")
    op.drop_constraint("fk_users_main_account", "users", type_="foreignkey")
    op.drop_constraint("users_phone_key", "users", type_="unique")
    for col in ["daily_limit", "main_account_id", "birth_date", "phone",
                "middle_name", "first_name", "last_name", "token_version"]:
        op.drop_column("users", col)
