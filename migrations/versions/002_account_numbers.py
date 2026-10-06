"""случайные номера счетов

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-03
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# имена fk постгрес сам придумал
FKS = [
    ("transfers", "transfers_from_account_id_fkey", "from_account_id"),
    ("transfers", "transfers_to_account_id_fkey", "to_account_id"),
    ("operations", "operations_account_id_fkey", "account_id"),
]


def upgrade():
    #номер генерим в коде, sequence не нужен
    op.execute("ALTER TABLE accounts ALTER COLUMN id DROP DEFAULT")
    op.execute("DROP SEQUENCE IF EXISTS accounts_id_seq")

    # cascade чтоб переводы тоже переехали на новый номер
    for table, name, col in FKS:
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(name, table, "accounts", [col], ["id"], onupdate="CASCADE")

    #старые 1, 2, 3... перенумеровываем
    op.execute("""
        UPDATE accounts SET id = 1000000000 + floor(random() * 9000000000)::bigint
        WHERE id < 1000000000
    """)


def downgrade():
    for table, name, col in FKS:
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(name, table, "accounts", [col], ["id"])
    # старые номера уже не вернёшь
    op.execute("CREATE SEQUENCE IF NOT EXISTS accounts_id_seq OWNED BY accounts.id")
    op.execute("SELECT setval('accounts_id_seq', (SELECT coalesce(max(id), 1) FROM accounts))")
    op.execute("ALTER TABLE accounts ALTER COLUMN id SET DEFAULT nextval('accounts_id_seq')")
