"""Roles de Administración: COMPRAS y CONTABILIDAD.

- COMPRAS: proveedores, órdenes de compra y facturas (la recepción la confirma TI).
- CONTABILIDAD: lectura de valores y exportaciones, sin datos personales.

Al bajar, los usuarios con estos roles pasan a CONSULTA y quedan inactivos
(CONSULTA ve el inventario, que estos roles no veían).

Revision ID: d4f5a6b7c8e9
Revises: c3e4f5a6b7d8
Create Date: 2026-10-08
"""
from typing import Sequence, Union

from alembic import op

revision: str = "d4f5a6b7c8e9"
down_revision: Union[str, Sequence[str], None] = "c3e4f5a6b7d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ROLES_NEW = ("\"USU_Rol\" IN ('SUPER_ADMIN', 'ADMIN_SEGURIDAD', 'JEFE_TI', 'ADMIN_TI', 'TECNICO', "
              "'AUDITOR', 'CONSULTA', 'COMPRAS', 'CONTABILIDAD')")
_ROLES_OLD = ("\"USU_Rol\" IN ('SUPER_ADMIN', 'ADMIN_SEGURIDAD', 'JEFE_TI', 'ADMIN_TI', 'TECNICO', "
              "'AUDITOR', 'CONSULTA')")


def upgrade() -> None:
    with op.batch_alter_table("INV_USUARIO") as batch:
        batch.drop_constraint("ck_usuario_rol_valido", type_="check")
        batch.create_check_constraint("ck_usuario_rol_valido", _ROLES_NEW)


def downgrade() -> None:
    op.execute("UPDATE \"INV_USUARIO\" SET \"USU_Estado\" = false, \"USU_Rol\" = 'CONSULTA' "
               "WHERE \"USU_Rol\" IN ('COMPRAS', 'CONTABILIDAD')")
    with op.batch_alter_table("INV_USUARIO") as batch:
        batch.drop_constraint("ck_usuario_rol_valido", type_="check")
        batch.create_check_constraint("ck_usuario_rol_valido", _ROLES_OLD)
