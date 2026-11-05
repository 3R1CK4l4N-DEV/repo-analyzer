import sqlalchemy as sa
from alembic import op


def upgrade():
    op.create_table("invoices", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("order_id", sa.Integer()))
    op.create_table("customers", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("email", sa.String(255)))
