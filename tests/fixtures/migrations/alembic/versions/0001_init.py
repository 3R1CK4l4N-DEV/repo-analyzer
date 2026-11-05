import sqlalchemy as sa
from alembic import op

revision = "0001"


def upgrade():
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner", sa.String(80), nullable=False),
        sa.Column("balance", sa.Numeric(12, 2)),
    )
    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2)),
        sa.PrimaryKeyConstraint("id"),
    )
    op.add_column("accounts", sa.Column("status", sa.String(10)))
    op.create_table("tmp", sa.Column("x", sa.Integer()))
    op.drop_table("tmp")


def downgrade():
    op.drop_table("transactions")
    op.drop_table("accounts")
