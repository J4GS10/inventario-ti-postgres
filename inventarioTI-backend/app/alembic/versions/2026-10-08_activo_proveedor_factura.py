"""Proveedor y factura del activo (plan de licencias y catálogos, bloque C, edición en lote).

Revision ID: c3e4f5a6b7d8
Revises: b8d2f3e4a5c6
Create Date: 2026-10-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3e4f5a6b7d8"
down_revision: Union[str, Sequence[str], None] = "b8d2f3e4a5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("INV_ACTIVO", sa.Column("PRV_Proveedor", sa.Integer(), nullable=True))
    op.add_column("INV_ACTIVO", sa.Column("ACT_Factura", sa.String(60), nullable=True))
    op.create_foreign_key("fk_activo_proveedor", "INV_ACTIVO", "INV_PROVEEDOR", ["PRV_Proveedor"], ["PRV_Proveedor"],
                          ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint("fk_activo_proveedor", "INV_ACTIVO", type_="foreignkey")
    op.drop_column("INV_ACTIVO", "ACT_Factura")
    op.drop_column("INV_ACTIVO", "PRV_Proveedor")
